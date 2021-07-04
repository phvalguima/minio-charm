# Copyright 2021 pguimaraes
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

import unittest
import os
import subprocess
from mock import patch
# from mock import PropertyMock

import src.charm as charm
from charm import MinioCharm
from ops.testing import Harness
from ops.model import BlockedStatus


# import wand.contrib.linux as linux
from wand.contrib.linux import groupAdd
import charmhelpers.fetch.ubuntu as ubuntu

TO_PATCH_LINUX = [
    "userAdd",
    "groupAdd",
]

TO_PATCH_FETCH = [
    'apt_install',
    'apt_update',
    'add_source'
]

TO_PATCH_HOST = [
    'service_running',
    'service_restart',
]


class TestCharm(unittest.TestCase):
    maxDiff = None

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
            patch("charm." + p)
#            self._patch(ubuntu, p)
        for p in TO_PATCH_HOST:
            self._patch(charm, p)

    @patch("ip.get_hostname")
    @patch("charm.userAdd")
    @patch("charm.groupAdd")
    @patch.object(subprocess, "check_output")
    @patch.object(os, "makedirs")
    # For config_change
    @patch.object(MinioCharm, "_check_if_ready_to_start")
    @patch.object(MinioCharm, "generate_certificates")
    @patch.object(MinioCharm, "generate_service_file_minio")
    @patch.object(MinioCharm, "generate_env_file_minio")
    @patch.object(MinioCharm, "_cert_relation_set")
    # Overall patchs
    @patch.object(charm, "render")
    @patch("charm.set_folders_and_permissions")
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
                                      mock_ip_get_hostname):
        mock_ip_get_hostname.return_value = "minio-0.test"
        mock_check_restart.return_value = False
        mock_certs.return_value = True
        self.harness = Harness(MinioCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.update_config({
            "package": "test",
            "user": "test",
            "group": "test",
            "min-units": 1
        })
        self.harness.begin_with_initial_hooks()
        minio = self.harness.charm
        minio._on_install(None)
        mock_perms.assert_called_once()
        # call_args_list is composed of:
        # [0] wget command
        # [1] dpkg command
        # .args always returns as a set (,), that is why 
        self.assertEqual(
            mock_check_output.call_args_list[1].args[0],
            ['wget', 'test', '-O', '/tmp/minio.deb'])

    @patch("charm.userAdd")
    @patch("charm.groupAdd")
    @patch.object(subprocess, "check_output")
    @patch.object(os, "makedirs")
    # For config_change
    @patch.object(MinioCharm, "_check_if_ready_to_start")
    @patch.object(MinioCharm, "generate_certificates")
    @patch.object(MinioCharm, "generate_env_file_minio")
    @patch.object(MinioCharm, "_cert_relation_set")
    # Overall patchs
    @patch.object(charm, "render")
    @patch("charm.set_folders_and_permissions")
    def test_config_cluster_and_conf_files(self,
                                           mock_perms,
                                           mock_render,
                                           mock_certs,
                                           mock_gen_file_minio,
                                           mock_gen_certs,
                                           mock_check_restart,
                                           mock_makedirs,
                                           mock_check_output,
                                           mock_group_add,
                                           mock_user_add):
        mock_check_restart.return_value = False
        self.harness = Harness(MinioCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()
        minio = self.harness.charm

        self.harness.update_config({
            "min-units": 4,
            "min-disks": 8
        })
        cluster_id = self.harness.add_relation("cluster", "minio")
        # Test if the cluster will block because of missing peers
        minio._on_config_changed(None)
        self.assertEqual(
            True, isinstance(minio.model.unit.status, BlockedStatus))
        
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
        minio._on_config_changed(None)
        print(mock_render.call_args_list)
