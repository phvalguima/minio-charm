#!/usr/bin/env python3
# Copyright 2021 pguimaraes
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk

"""Charm the service.

Refer to the following post for a quick-start guide that will help you
develop a new k8s charm using the Operator Framework:

    https://discourse.charmhub.io/t/4208
"""

import subprocess
import logging
import socket
import json
import os
import base64

from ops.charm import CharmBase, InstallEvent
from ops.framework import StoredState
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus

from wand.apps.relations.tls_certificates import (
    TLSCertificateRequiresRelation,
    TLSCertificateDataNotFoundInRelationError,
    TLSCertificateRelationNotPresentError
)

from charmhelpers.fetch.ubuntu import (
    apt_update
)

from wand.contrib.disk_map import DiskMapHelper

from wand.contrib.linux import (
    userAdd,
    groupAdd,
    LinuxUserAlreadyExistsError,
    LinuxGroupAlreadyExistsError,
    set_folders_and_permissions
)

from wand.security.ssl import (
    genRandomPassword,
    saveCrtChainToFile
)

from wand.contrib.coordinator import (
    RestartCharmEvent,
    OpsCoordinator
)
from wand.security.ssl import _break_crt_chain

from charmhelpers.core.host import (
    service_running,
    service_restart
)

from charmhelpers.core.templating import render

from cluster import (
    MinioClusterManager,
    MinioClusterNumDisksMustBeDivisibleBy4
)
from lib.charms.minio.v1.object_storage import ObjectStorageRelationProvider

from nrpe.client import NRPEClient
from monitoring import PrometheusMonitorCluster, PrometheusMonitorNode

DISK_LAYOUT = """- /data1:
  - fs-type: ext4
  - options: ''
- /data2:
  - fs-type: ext4
  - options: ''
- /data3:
  - fs-type: ext4
  - options: ''
- /data4:
  - fs-type: ext4
  - options: ''
- /data5:
  - fs-type: ext4
  - options: ''
- /data6:
  - fs-type: ext4
  - options: ''
- /data7:
  - fs-type: ext4
  - options: ''
- /data8:
  - fs-type: ext4
  - options: ''
- /data9:
  - fs-type: ext4
  - options: ''
- /data10:
  - fs-type: ext4
  - options: ''
- /data11:
  - fs-type: ext4
  - options: ''
- /data12:
  - fs-type: ext4
  - options: ''
- /data13:
  - fs-type: ext4
  - options: ''
- /data14:
  - fs-type: ext4
  - options: ''
- /data15:
  - fs-type: ext4
  - options: ''
- /data16:
  - fs-type: ext4
  - options: ''
- /data17:
  - fs-type: ext4
  - options: ''
- /data18:
  - fs-type: ext4
  - options: ''
- /data19:
  - fs-type: ext4
  - options: ''
- /data20:
  - fs-type: ext4
  - options: ''
- /data21:
  - fs-type: ext4
  - options: ''
- /data22:
  - fs-type: ext4
  - options: ''
- /data23:
  - fs-type: ext4
  - options: ''
- /data24:
  - fs-type: ext4
  - options: ''
- /data25:
  - fs-type: ext4
  - options: ''
- /data26:
  - fs-type: ext4
  - options: ''
- /data27:
  - fs-type: ext4
  - options: ''
- /data28:
  - fs-type: ext4
  - options: ''
- /data29:
  - fs-type: ext4
  - options: ''
- /data30:
  - fs-type: ext4
  - options: ''
- /data31:
  - fs-type: ext4
  - options: ''
- /data32:
  - fs-type: ext4
  - options: ''"""

logger = logging.getLogger(__name__)

# As described on:
# https://github.com/minio/minio/tree/master/docs/tls/
#    kubernetes#3-update-deployment-yaml-file
# and
# https://docs.min.io/docs/how-to-secure-access-to-minio-server-with-tls.html
TLS_PATH = "/home/{}/.minio/certs/"
CA_CERT_PATH = "/home/{}/.minio/certs/CAs/"
CONFIG_ENV_FILE = "/etc/minio"
SVC_FILE = "/etc/systemd/system/minio.service"


class MinioCharm(CharmBase):
    """Charm the Minio for Baremetal and VM."""
    on = RestartCharmEvent()

    _stored = StoredState()

    def __init__(self, *args):
        super().__init__(*args)
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(
            self.on.config_changed, self._on_config_changed)
        self.framework.observe(
            self.on.upgrade_action, self._on_upgrade_action)
        self.framework.observe(
            self.on.cluster_relation_joined,
            self._on_cluster_relation_joined)
        self.framework.observe(
            self.on.cluster_relation_changed,
            self._on_cluster_relation_changed)

        self.framework.observe(
            self.on.certificates_relation_joined,
            self._on_certificates_relation_joined)
        self.framework.observe(
            self.on.certificates_relation_changed,
            self._on_certificates_relation_changed)

        self.framework.observe(
            self.on.object_storage_relation_joined,
            self._on_object_storage_relation_joined)
        self.framework.observe(
            self.on.object_storage_relation_changed,
            self._on_object_storage_relation_changed)

        self.framework.observe(
            self.on.prometheus_manual_relation_joined,
            self._on_prometheus_relation_joined)
        self.framework.observe(
            self.on.prometheus_manual_relation_changed,
            self._on_prometheus_relation_changed)

        self.framework.observe(self.on.restart_event,
                                self._on_restart_event)

        self.nrpe = NRPEClient(self, 'nrpe-external-master')
        self.framework.observe(self.nrpe.on.nrpe_available, self.on_nrpe_available)

        self.framework.observe(self.on.update_status,
                               self.on_update_status)
        self.minio = ObjectStorageRelationProvider(self, "object-storage")
        self.cluster = MinioClusterManager(self, "cluster", None, "data")
        self.certificates = \
            TLSCertificateRequiresRelation(self, 'certificates')
        self._stored.set_default(package="")
        self._stored.set_default(ctx="{}")
        self.services = ["minio"]
        # DiskMapHelper expects a map of folder names and equivalent
        # mounting options to be used for the disks mounted via juju
        # storage. Given we need some previsiblity on the naming of those
        # disks, the charm itself will set 32 different mount names.
        self._stored.set_default(disks=DISK_LAYOUT)
        self.disks = DiskMapHelper(
            self, self._stored.disks, "data",
            self.config["user"], self.config["group"])
        self._stored.set_default(minio_root_pwd=genRandomPassword())

    def _on_restart_event(self, event):
        if event.restart():
            # Restart was successful, if the charm is keeping track
            # of a context, that is the place it should be updated
            self._stored.ctx = event.ctx
        else:
            # defer the RestartEvent as it is still waiting for the
            # lock to be released.
            event.defer()

    def on_nrpe_available(self, event):
        check_name = "check_{}".format(
            self.model.unit.name.replace("/", "_"))

        self.nrpe.add_check(command=[
            '/usr/lib/nagios/plugins/check_tcp',
            '-H', self.minio.adverise_addr,
            '-p', str(self.model.config['minio-service-port']),
        ], name=check_name)

        # Save all new checks to filesystem and to Nagios
        self.nrpe.commit()

    def _on_prometheus_relation_joined(self, event):
        if self.unit.is_leader():
            # The leader submit the cluster-wide entry for prometheus
            endpoint = self.minio.hostname or None
            p = PrometheusMonitorCluster(self.charm, 'prometheus-manual')
            p.request(
                self.config["prometheus_port"],
                metrics_path=self.config["prometheus_metrics_path"],
                endpoint=endpoint,
                ca_cert=self.get_ssl_cacert()
                if len(self.get_ssl_cacert()) > 0 else None)
        # Every node should submit a "node" entry for prometheus
        endpoint = self.minio.hostname or None
        p = PrometheusMonitorNode(self.charm, 'prometheus-manual')
        p.request(
            self.config["prometheus_port"],
            metrics_path=self.config["prometheus_metrics_path"],
            endpoint=endpoint,
            ca_cert=self.get_ssl_cacert()
            if len(self.get_ssl_cacert()) > 0 else None)

    def _on_prometheus_relation_changed(self, event):
        return

    def _on_object_storage_relation_joined(self, event):
        # TODO: Implement this relation
        return

    def _on_object_storage_relation_changed(self, event):
        # TODO: Implement this relation
        return

    def _on_certificates_relation_joined(self, event):
        if not self._cert_relation_set(event):
            return
        self._on_config_changed(event)

    def _on_certificates_relation_changed(self, event):
        if not self._cert_relation_set(event):
            return
        self._on_config_changed(event)

    def on_update_status(self, event):
        """ This method will update the status of the charm according
            to the app's status"""
        if isinstance(self.model.unit.status, MaintenanceStatus):
            # Log the fact the unit is already blocked and return
            logger.warn(
                "update-status called but unit is in maintenance "
                "status, with message {}, return".format(
                    self.model.unit.status.message))
            return
        svc_list = [s for s in self.services if not service_running(s)]
        if len(svc_list) == 0:
            self.model.unit.status = \
                ActiveStatus("{} is running".format(self.services))
            # The status is not in Maintenance and we can see the service
            # is up, therefore we can switch to Active.
            return
        if isinstance(self.model.unit.status, BlockedStatus):
            # Log the fact the unit is already blocked and return
            logger.warn(
                "update-status called but unit is in blocked "
                "status, with message {}, return".format(
                    self.model.unit.status.message))
            return
        self.model.unit.status = \
            BlockedStatus("Services not running that"
                          " should be: {}".format(",".join(svc_list)))

    @property
    def ctx(self):
        return json.loads(self._stored.ctx)

    @ctx.setter
    def ctx(self, c):
        self._stored.ctx = json.dumps(c)

    def _on_cluster_relation_joined(self, event):
        if self.unit.is_leader():
            # First contact with the cluster, the leade rneeds to set
            # the root password to its units
            self.cluster.set_root_pwd(
                self._stored.minio_root_pwd)
        self._on_cluster_relation_changed(event)

    def _on_cluster_relation_changed(self, event):
        if not self._cert_relation_set(event):
            return
        self.cluster.relation_changed(event)
        self._on_config_changed(event)

    def _on_install(self, event):
        folders = [
            "/etc/minio"
            "/home/{}".format(self.config["user"]),
            "/home/{}/.minio/".format(self.config["user"]),
            "/home/{}/.minio/certs".format(self.config["user"]),
            TLS_PATH.format(self.config["user"]),
            "/var/log/minio",
            CA_CERT_PATH.format(self.config["user"])]
        os.makedirs(folders, exist_ok=True)
        try:
            groupAdd(self.config["group"], system=True)
        except LinuxGroupAlreadyExistsError:
            pass
        try:
            userAdd(self.config["user"], group=self.config["group"])
        except LinuxUserAlreadyExistsError:
            pass
        set_folders_and_permissions(
            folders, self.config["user"], self.config["group"])
        # This is the very first hook, we do not need to wait,
        # for an action, just run the installation process.
        self._do_install_or_upgrade()

    def _on_upgrade_action(self, event):
        self._do_install_or_upgrade()

    def _do_install_or_upgrade(self):
        """Runs the install or upgrade of the minio and nginx packages."""
        # apt upate before starting the package install/upgrades
        apt_update()
        # minio install/upgrade
        try:
            subprocess.check_output([
                "wget", self.config.get("package", ""),
                "-O", "/tmp/minio.deb"
            ])
            subprocess.check_output([
                "dpkg", "-i", "/tmp/minio.deb"
            ])
        except subprocess.CalledProcessError as e:
            logger.error("Installation of minio package failed with {}".format(str(e)))
        self._stored.package = self.config.get("package", "")
        """
        # Now, install or upgrade nginx
        cmd = ["apt", "install", '--assume-yes']
        if get_installed_version("nginx"):
            cmd.append("--only-upgrade")
        cmd.append("nginx")
        try:
            subprocess.check_output(cmd)
        except subprocess.CalledProcessError as e:
            logger.error("Installation of nginx package failed with {}".format(str(e)))
        """

    def _check_if_ready_to_start(self, ctx):
        # ctx can be a string or dict, then check and convert accordingly
        c = json.dumps(ctx) if isinstance(ctx, dict) else ctx
        if c == self._stored.ctx:
            logger.debug("Current state: {}, saved state: {}".format(
                c, self._stored.ctx))
            return False
        return True

    def _on_config_changed(self, event):
        """CONFIG CHANGE
        1) Treat the case we are dealing with an upgrade
        2) Check if we can do a config change or are we waiting for sth:
        2.2) Ensure cluster relation has the correct URL and volumes
        2.1) Check if cluster relation is ready if min-units > 1
        2.1.1) If min-units > 1: check if password available on cluster
        2.3) Check certificates
        3) Initiate context
        4) Generate the environment file for Minio
        5) Restart strategy
        """

        # 1) Treat the case where we are in the middle of an upgrade
        if self._stored.package != self.config["package"]:
            # Operator specified a new package, upgrade time
            # This if should block the application until the operator
            # runs the action to do the upgrade or if automatic-upgrade
            # is true.
            if self.config.get("automatic-upgrade", False):
                self._do_install_or_upgrade(self.config["package"])
            else:
                self.model.unit.status = BlockedStatus(
                    "package config changed: waiting for upgrade action...")
                return
        # 2) Check if we can do a config change or waiting for sth
        # 2.1) Check cluster relation readiness
        try:
            if self.config["min-units"] > 1:
                if not self.cluster.is_ready():
                    self.model.unit.status = BlockedStatus(
                        "Waiting for peers")
                    # No need to defer this event
                    return
        except MinioClusterNumDisksMustBeDivisibleBy4 as e:
            self.model.unit.status = BlockedStatus(str(e))
            # Operator must do a change, which will retrigger this logic
            # No need to defer this event
            return
        # 2.1.1) If min-units > 1: check if password available on cluster
        if not self.unit.is_leader():
            self._stored.minio_root_pwd = self.cluster.get_root_pwd()
        if self.cluster.relations:
            # 2.2) Ensure cluster relation has the correct URL for this unit
            self.cluster.url = "{}://{}:{}".format(
                "https" if len(self.get_ssl_cert()) > 0 else "http",
                self.minio.hostname(), self.config["minio-service-port"])
            self.cluster.used_folders = self.disks.used_folders()
        # 2.3) Check certificates
        if not self._cert_relation_set(event):
            return
        ctx = {}
        ctx["env_minio"] = self.generate_env_file_minio()
        ctx["minio_svc"] = self.generate_service_file_minio()
        ctx["cert_data"] = self.generate_certificates()

        if self.unit.is_leader():
            # Now, we need to always handle the locks, even if acquire() was not
            # called since _check_if_ready_to_start returned False.
            # Therefore, we need to manually handle those locks.
            # If _check_if_ready_to_start returns True, then the locks will be
            # managed at the restart event and config-changed is closed with a
            # return.
            coordinator = OpsCoordinator()
            coordinator.resume()
            coordinator.release()

        # Check if the unit has never been restarted (running InstallEvent).
        # In these cases, there is no reason to
        # request for the a restart to the cluster, instead simply restart.
        # For the "failed" case, check if service-restart-failed is set
        # if so, restart it.
        if isinstance(event, InstallEvent):
            service_restart(self.service)
            self.model.unit.status = \
                ActiveStatus("Service is running")
            return

        # Now, restart service
        self.model.unit.status = \
            MaintenanceStatus("Building context...")
        logger.debug("Context: {}, saved state is: {}".format(
            ctx, self._stored.ctx))
        if self._check_if_ready_to_start(ctx):
            self.on.restart_event.emit(ctx, services=self.services)
            self.model.unit.status = \
                BlockedStatus("Waiting for restart event")
            return
        elif service_running(self.service):
            self.model.unit.status = \
                ActiveStatus("Service is running")
        else:
            self.model.unit.status = \
                BlockedStatus("Service not running that "
                              "should be: {}".format(self.services))

    def generate_certificates(self):
        """Generate the certificates: CA, cert and key files obtained
        either by relations or config.
        """
        user = self.config["user"]
        group = self.config["group"]
        ctx = {}
        ctx["cert"] = self.get_ssl_cert()
        ctx["key"] = self.get_ssl_key()
        saveCrtChainToFile(
            self.get_ssl_cert(),
            cert_path=TLS_PATH.format(user) + "cert.crt",
            ca_chain_path=CA_CERT_PATH.format(user) + "ca.crt",
            user=user, group=group)
        with open(TLS_PATH.format(user), "w") as f:
            f.write(self.get_ssl_key())
            f.close()
        return ctx

    def generate_service_file_minio(self):
        """Generate the service file with right user and group
        """
        svc = {}
        svc["user"] = self.config["user"]
        svc["group"] = self.config["group"]

        render(source="minio.service.j2",
               target=SVC_FILE,
               owner="root",
               group="root",
               perms=0o644,
               context={
                   "svc": svc
               })
        return svc

    def generate_env_file_minio(self):
        """Generate the env file that will be present on /etc/default

        1) resolve the volumes if we have a cluster or not
        2) MINIO_OPTS: setup the port and EC parity
        3) Set root user credentials
        4) Set Prometheus credentials if relation is stablished
        """
        env = {}
        env["MINIO_VOLUMES"] = []
        if self.cluster.relation:
            # We have a cluster, then pick info for each unit
            for k, v in self.cluster.endpoints().items():
                env["MINIO_VOLUMES"].append(
                    "{}/{}".format(k, x) for x in v)
        else:
            # We do not have a cluster, just pick the used folders
            for x in self.disks.used_folders():
                env["MINIO_VOLUMES"].append(
                    "{}".format(x))
        env["MINIO_OPTS"] = "--address :{}".format(
            self.config["minio-service-port"])
        env["MINIO_ROOT_USER"] = self.config["minio_root_user"]
        env["MINIO_ROOT_PASSWORD"] = self._stored.minio_root_pwd
        if self.prometheus.relations:
            env["MINIO_PROMETHEUS_AUTH_TYPE"] = "public"
        render(source="minio_env",
               target=CONFIG_ENV_FILE,
               owner=self.config['user'],
               group=self.config["group"],
               perms=0o600,
               context={
                   "env": env
               })
        return env

    def get_ssl_cacert(self):
        return "".join(_break_crt_chain(self.get_ssl_cert())[1:])

    def get_ssl_cert(self):
        return self._get_ssl(self.minio, "cert")

    def get_ssl_key(self):
        return self._get_ssl(self.minio, "key")

    def _get_ssl(self, relation, ty):
        """Recover the SSL certs based on the relation"""

        prefix = None
        if isinstance(relation, ObjectStorageRelationProvider):
            prefix = "ssl"
        if len(self.config.get(prefix + "_cert", "")) > 0 and \
           len(self.config.get(prefix + "_key", "")) > 0:
            if ty == "cert":
                return base64.b64decode(
                    self.config[prefix + "_cert"]).decode("ascii")
            else:
                return base64.b64decode(
                    self.config[prefix + "_key"]).decode("ascii")
        try:
            certs = self.certificates.get_server_certs()
        except TLSCertificateRelationNotPresentError as e:
            # No relation for certificates present and no configs set
            # Return None for this request
            return ""
        c = certs[relation.binding_addr][ty]
        if ty == "cert":
            c = c + \
                self.certificates.get_chain()
        logger.debug("SSL {} for {}"
                     " from tls-certificates: {}".format(ty, prefix, c))
        return c

    def _cert_relation_set(self, event, rel=None):
        # generate cert request if tls-certificates available
        # rel may be set to None in cases such as config-changed
        # or install events. In these cases, the goal is to run
        # the validation at the end of this method
        if rel:
            if self.certificates.relation and rel.relation:
                sans = [
                    rel.binding_addr,
                    rel.advertise_addr,
                    rel.hostname,
                    socket.gethostname()
                ]
                # Add the service-* info
                if len(self.config["service-url"]) > 0:
                    sans.append(self.config["service-url"])
                if len(self.config["service-vip"]) > 0:
                    sans.append(self.config["service-vip"])
                # Common name is always CN as this is the element
                # that organizes the cert order from tls-certificates
                self.certificates.request_server_cert(
                    cn=rel.binding_addr,
                    sans=sans)
            logger.info("Either certificates "
                        "relation not ready or not set")
        # This try/except will raise an exception if tls-certificate
        # is set and there is no certificate available on the relation yet.
        # That will also cause the
        # event to be deferred, waiting for certificates relation to finish
        # If tls-certificates is not set, then the try will run normally,
        # either marking there is no certificate configuration set or
        # concluding the method.
        try:
            if (not self.get_ssl_cert() or not self.get_ssl_key()):
                self.model.unit.status = \
                    BlockedStatus("Waiting for certificates "
                                  "relation or option")
                logger.info("Waiting for certificates relation "
                            "to publish data")
                return False
        # These excepts will treat the case tls-certificates relation is used
        # but the relation is not ready yet
        # KeyError is also a possibility, if get_ssl_cert is called before any
        # event that actually submits a request for a cert is done
        except (TLSCertificateDataNotFoundInRelationError,
                TLSCertificateRelationNotPresentError,
                KeyError):
            self.model.unit.status = \
                BlockedStatus("There is no certificate option or "
                              "relation set, waiting...")
            logger.warning("There is no certificate option or "
                           "relation set, waiting...")
            if event:
                event.defer()
            return False
        return True


if __name__ == "__main__":
    main(MinioCharm)
