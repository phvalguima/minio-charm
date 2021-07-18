# Copyright 2021 pguimaraes
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

import unittest
import os
import subprocess
import socket
from mock import patch, PropertyMock

# Do not import MinioCharm, it will confuse the patchs
import src.charm as charm
from ops.testing import Harness
from ops.model import BlockedStatus

import wand.contrib.disk_map as disk_map

import charms.minio.v1.object_storage as obj_stor

TO_PATCH_LINUX = [
    "userAdd",
    "groupAdd",
]

TO_PATCH_FETCH = [
    'apt_update',
]

TO_PATCH_HOST = [
    'service_running',
    'service_restart',
]


class TestCharm(unittest.TestCase):
    maxDiff = None

    def _order_units_data(self, rel_name, harness_obj):
        """The way units are added to the units list of a relation is not
        predictable. That means runs of the same test will vary if several
        units are added to the same relation, for example.

        This method orders the units of a given relation.
        However, this method will change the units from set to list type.
        """
        harness_obj._model._relations._data[rel_name][0].units = \
            sorted(
                harness_obj._model._relations._data[rel_name][0].units,
                key=lambda x: x.name)
        return harness_obj

    def _patch(self, obj, method):
        _m = patch.object(obj, method)
        mock = _m.start()
        self.addCleanup(_m.stop)
        return mock

    def _simulate_render(self, ctx=None, templ_file=""):
        import jinja2
        env = jinja2.Environment(loader=jinja2.FileSystemLoader('templates'))
        templ = env.get_template(templ_file)
        doc = templ.render(ctx)
        return doc

    def setUp(self):
        super().setUp()
        for p in TO_PATCH_LINUX:
            self._patch(charm, p)
        for p in TO_PATCH_FETCH:
            self._patch(charm, p)
        for p in TO_PATCH_HOST:
            self._patch(charm, p)

    @patch.object(charm, "open_port")
    @patch.object(charm, "close_port")
    @patch("charm.apt_update")
    @patch.object(obj_stor.ObjectStorageRelationProvider,
                  "advertise_addr",
                  new_callable=PropertyMock)
    @patch.object(obj_stor, "get_hostname")
    @patch("charm.userAdd")
    @patch("charm.groupAdd")
    @patch.object(subprocess, "check_output")
    @patch.object(os, "makedirs")
    # For config_change
    @patch.object(charm.MinioCharm, "_check_if_need_restart")
    @patch.object(charm.MinioCharm, "generate_certificates")
    @patch.object(charm.MinioCharm, "generate_service_file_minio")
    @patch.object(charm.MinioCharm, "generate_env_file_minio")
    @patch.object(charm.MinioCharm, "_cert_relation_set")
    # Overall patchs
    @patch.object(charm, "render")
    @patch.object(charm, "set_folders_and_permissions")
    def test_install_user_permissions(self,
                                      mock_perms,
                                      mock_render,
                                      mock_certs,
                                      mock_gen_file_minio,
                                      mock_gen_svc_minio,
                                      mock_gen_certs,
                                      mock_check_restart,
                                      mock_makedirs,
                                      mock_check_output,
                                      mock_group_add,
                                      mock_user_add,
                                      mock_ip_get_hostname,
                                      mock_advertise_addr,
                                      mock_apt_update,
                                      mock_open_port,
                                      mock_close_port):
        mock_ip_get_hostname.return_value = "minio-0.test"
        mock_check_restart.return_value = False
        mock_certs.return_value = True
        self.harness = Harness(charm.MinioCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.update_config({
            "package": "test",
            "user": "test",
            "group": "test",
            "min-units": 1
        })
        self.harness.begin_with_initial_hooks()
        mock_perms.assert_called_once()
        # call_args_list is composed of:
        # [0] wget command
        # [1] dpkg command
        # .args always returns as a set (,), that is why
        self.assertEqual(
            mock_check_output.call_args_list[0].args[0],
            ['wget', 'test', '-O', '/tmp/minio.deb'])

    @patch.object(charm, "open_port")
    @patch.object(charm, "close_port")
    @patch.object(disk_map, "create_dir")
    @patch.object(obj_stor.ObjectStorageRelationProvider,
                  "advertise_addr",
                  new_callable=PropertyMock)
    @patch.object(obj_stor, "get_hostname")
    @patch("charm.userAdd")
    @patch("charm.groupAdd")
    @patch.object(subprocess, "check_output")
    @patch.object(os, "makedirs")
    # For config_change
    @patch.object(charm.MinioCharm, "_check_if_need_restart")
    @patch.object(charm.MinioCharm, "generate_certificates")
    @patch.object(charm.MinioCharm, "generate_env_file_minio")
    @patch.object(charm.MinioCharm, "_cert_relation_set")
    # Overall patchs
    @patch.object(charm, "render")
    @patch.object(charm, "set_folders_and_permissions")
    def test_cluster_block_missing_neighb(self,
                                          mock_perms,
                                          mock_render,
                                          mock_certs,
                                          mock_gen_file_minio,
                                          mock_gen_certs,
                                          mock_check_restart,
                                          mock_makedirs,
                                          mock_check_output,
                                          mock_group_add,
                                          mock_user_add,
                                          mock_ip_get_hostname,
                                          mock_advertise_addr,
                                          mock_create_dir,
                                          mock_open_port,
                                          mock_close_port):
        mock_ip_get_hostname.return_value = "minio-0.test"
        mock_check_restart.return_value = False
        self.harness = Harness(charm.MinioCharm)
        self.harness.add_storage("data", 2)
        self.harness.add_relation("cluster", "minio")
        self.harness.update_config({
            "min-units": 4,
            "min-disks": 8
        })
        self.harness.begin_with_initial_hooks()
        self.addCleanup(self.harness.cleanup)
        minio = self.harness.charm
        # Test if the cluster will block because of missing peers
        minio._on_config_changed(None)
        self.assertEqual(
            True, isinstance(minio.model.unit.status, BlockedStatus))

    @patch.object(charm, "open_port")
    @patch.object(charm, "close_port")
    @patch.object(disk_map, "create_dir")
    @patch.object(obj_stor.ObjectStorageRelationProvider,
                  "advertise_addr",
                  new_callable=PropertyMock)
    @patch.object(obj_stor, "get_hostname")
    @patch("charm.userAdd")
    @patch("charm.groupAdd")
    @patch.object(subprocess, "check_output")
    @patch.object(os, "makedirs")
    # For config_change
    @patch.object(charm.MinioCharm, "_check_if_need_restart")
    @patch.object(charm.MinioCharm, "generate_certificates")
    @patch.object(charm.MinioCharm, "generate_env_file_minio")
    @patch.object(charm.MinioCharm, "_cert_relation_set")
    # Overall patchs
    @patch.object(charm, "render")
    @patch.object(charm, "set_folders_and_permissions")
    def test_config_cluster_and_svc_file(self,
                                         mock_perms,
                                         mock_render,
                                         mock_certs,
                                         mock_gen_file_minio,
                                         mock_gen_certs,
                                         mock_check_restart,
                                         mock_makedirs,
                                         mock_check_output,
                                         mock_group_add,
                                         mock_user_add,
                                         mock_ip_get_hostname,
                                         mock_advertise_addr,
                                         mock_create_dir,
                                         mock_open_port,
                                         mock_close_port):
        mock_ip_get_hostname.return_value = "minio-0.test"
        mock_check_restart.return_value = False
        self.harness = Harness(charm.MinioCharm)
        self.harness.add_storage("data", 2)
        cluster_id = self.harness.add_relation("cluster", "minio")
        self.harness.update_config({
            "min-units": 4,
            "min-disks": 8
        })
        # Complete the cluster
        self.harness.add_relation_unit(cluster_id, "minio/1")
        self.harness.update_relation_data(cluster_id, "minio/1", {
            "num_disks": "2",
            "url": "http://minio-1.test:9000",
            "used_folders": "/data1,/data2"
        })
        self.harness.add_relation_unit(cluster_id, "minio/2")
        self.harness.update_relation_data(cluster_id, "minio/2", {
            "num_disks": "2",
            "url": "http://minio-2.test:9000",
            "used_folders": "/data1,/data2"
        })
        self.harness.add_relation_unit(cluster_id, "minio/3")
        self.harness.update_relation_data(cluster_id, "minio/3", {
            "num_disks": "2",
            "url": "http://minio-3.test:9000",
            "used_folders": "/data1,/data2"
        })
        self.harness.begin_with_initial_hooks()
        self.addCleanup(self.harness.cleanup)
        minio = self.harness.charm
        minio._on_config_changed(None)
        mock_render.assert_called_with(
            source='minio.service.j2',
            target='/etc/systemd/system/minio.service',
            owner='root', group='root', perms=420,
            context={'svc': {'user': 'minio', 'group': 'minio'}}
        )

    @patch.object(charm, "open_port")
    @patch.object(charm, "close_port")
    @patch.object(disk_map, "create_dir")
    @patch.object(obj_stor.ObjectStorageRelationProvider,
                  "advertise_addr",
                  new_callable=PropertyMock)
    @patch.object(obj_stor, "get_hostname")
    @patch("charm.userAdd")
    @patch("charm.groupAdd")
    @patch.object(subprocess, "check_output")
    @patch.object(os, "makedirs")
    # For config_change
    @patch.object(charm.MinioCharm, "_check_if_need_restart")
    @patch.object(charm.MinioCharm, "generate_certificates")
    @patch.object(charm.MinioCharm, "generate_service_file_minio")
    @patch.object(charm.MinioCharm, "_cert_relation_set")
    # Overall patchs
    @patch.object(charm, "render")
    @patch.object(charm, "set_folders_and_permissions")
    @patch.object(charm, "genRandomPassword")
    @patch.object(charm, "OpsCoordinator")
    def test_config_cluster_and_env_file(self,
                                         mock_ops_coordinator,
                                         mock_gen_random,
                                         mock_perms,
                                         mock_render,
                                         mock_certs,
                                         mock_gen_svc_minio,
                                         mock_gen_certs,
                                         mock_check_restart,
                                         mock_makedirs,
                                         mock_check_output,
                                         mock_group_add,
                                         mock_user_add,
                                         mock_ip_get_hostname,
                                         mock_advertise_addr,
                                         mock_create_dir,
                                         mock_open_port,
                                         mock_close_port):
        mock_gen_random.return_value = "testtest"
        mock_ip_get_hostname.return_value = "minio-0.test"
        mock_check_restart.return_value = False
        self.harness = Harness(charm.MinioCharm)
        self.harness.add_storage("data", 2)
        self.addCleanup(self.harness.cleanup)
        # Start the unit's relations and leader settings
        self.harness.set_leader(True)
        cluster_id = self.harness.add_relation("cluster", "minio")
        self.harness.update_config({
            "min-units": 4,
            "min-disks": 8
        })
        # Complete the cluster
        self.harness.add_relation_unit(cluster_id, "minio/1")
        self.harness.update_relation_data(cluster_id, "minio/1", {
            "num_disks": "2",
            "url": "http://minio-1.test:9000",
            "used_folders": "/data1,/data2"
        })
        self.harness.add_relation_unit(cluster_id, "minio/2")
        self.harness.update_relation_data(cluster_id, "minio/2", {
            "num_disks": "2",
            "url": "http://minio-2.test:9000",
            "used_folders": "/data1,/data2"
        })
        self.harness.add_relation_unit(cluster_id, "minio/3")
        self.harness.update_relation_data(cluster_id, "minio/3", {
            "num_disks": "2",
            "url": "http://minio-3.test:9000",
            "used_folders": "/data1,/data2"
        })
        self.harness.set_leader(True)
        self.harness.begin_with_initial_hooks()
        # Reorder the cluster units set so we ensure test consistency
        # across several runs and rerun the generate env method
        self.harness = self._order_units_data('cluster', self.harness)
        self.harness.charm.generate_env_file_minio()
        mock_render.assert_called_with(
            source='minio_env', target='/etc/minio/minio',
            owner='minio', group='minio', perms=384,
            context={
                'env': {
                    'MINIO_VOLUMES': "\"http://minio-0.test:9000/data1 "
                                     "http://minio-0.test:9000/data2 "
                                     "http://minio-1.test:9000/data1 "
                                     "http://minio-1.test:9000/data2 "
                                     "http://minio-2.test:9000/data1 "
                                     "http://minio-2.test:9000/data2 "
                                     "http://minio-3.test:9000/data1 "
                                     "http://minio-3.test:9000/data2\"",
                    'MINIO_OPTS': '"--address :9000"',
                    'MINIO_ROOT_USER': 'minioadmin',
                    'MINIO_ROOT_PASSWORD': 'testtest'}})

    @patch.object(charm, "open_port")
    @patch.object(charm, "close_port")
    @patch.object(disk_map, "create_dir")
    @patch.object(obj_stor.ObjectStorageRelationProvider,
                  "advertise_addr",
                  new_callable=PropertyMock)
    @patch.object(obj_stor, "get_hostname")
    @patch("charm.userAdd")
    @patch("charm.groupAdd")
    @patch.object(subprocess, "check_output")
    @patch.object(os, "makedirs")
    # For config_change
    @patch.object(charm.MinioCharm, "_check_if_need_restart")
    @patch.object(charm.MinioCharm, "generate_env_file_minio")
    @patch.object(charm.MinioCharm, "generate_service_file_minio")
    # Overall patchs
    @patch.object(charm, "render")
    @patch.object(charm, "set_folders_and_permissions")
    @patch.object(charm, "genRandomPassword")
    @patch.object(charm, "OpsCoordinator")
    # For certificates relation
    @patch.object(obj_stor.ObjectStorageRelationProvider,
                  "binding_addr",
                  new_callable=PropertyMock)
    @patch.object(socket, "gethostname")
    @patch.object(charm, "saveCrtChainToFile")
    @patch.object(charm, "open")
    def test_config_cluster_cert_relatio(self,
                                         mock_open,
                                         mock_save_crt_chain_file,
                                         mock_socket_hostname,
                                         mock_binding_addr,
                                         mock_ops_coordinator,
                                         mock_gen_random,
                                         mock_perms,
                                         mock_render,
                                         mock_gen_svc_minio,
                                         mock_env_minio,
                                         mock_check_restart,
                                         mock_makedirs,
                                         mock_check_output,
                                         mock_group_add,
                                         mock_user_add,
                                         mock_ip_get_hostname,
                                         mock_advertise_addr,
                                         mock_create_dir,
                                         mock_open_port,
                                         mock_close_port):
        CERT = "-----BEGIN CERTIFICATE-----\n" + \
               "certificate\n-----END CERTIFICATE-----\n"
        mock_binding_addr.return_value = "127.0.0.1"
        mock_advertise_addr.return_value = "127.0.0.1"
        mock_gen_random.return_value = "testtest"
        mock_ip_get_hostname.return_value = "minio-0.test"
        mock_socket_hostname.return_value = "minio-0.test"
        mock_check_restart.return_value = False
        self.harness = Harness(charm.MinioCharm)
        self.harness.add_storage("data", 2)
        cluster_id = self.harness.add_relation("cluster", "minio")
        self.harness.update_config({
            "min-units": 4,
            "min-disks": 8
        })
        # Complete the cluster
        self.harness.add_relation_unit(cluster_id, "minio/1")
        self.harness.update_relation_data(cluster_id, "minio/1", {
            "num_disks": "2",
            "url": "http://minio-1.test:9000",
            "used_folders": "/data1,/data2"
        })
        self.harness.add_relation_unit(cluster_id, "minio/2")
        self.harness.update_relation_data(cluster_id, "minio/2", {
            "num_disks": "2",
            "url": "http://minio-2.test:9000",
            "used_folders": "/data1,/data2"
        })
        self.harness.add_relation_unit(cluster_id, "minio/3")
        self.harness.update_relation_data(cluster_id, "minio/3", {
            "num_disks": "2",
            "url": "http://minio-3.test:9000",
            "used_folders": "/data1,/data2"
        })
        self.harness.set_leader(True)
        cert_id = self.harness.add_relation("certificates", "easyrsa")
        self.harness.add_relation_unit(cert_id, "easyrsa/0")
        self.harness.update_relation_data(cert_id, "easyrsa/0", {
            "minio_0.server.cert": CERT,
            "minio_0.server.key": "key"
        })
        self.harness.begin_with_initial_hooks()
        self.addCleanup(self.harness.cleanup)
        minio = self.harness.charm
        self.assertEqual(
            minio.get_ssl_cert(),
            CERT)
        self.assertEqual(
            minio.get_ssl_key(), "key")
