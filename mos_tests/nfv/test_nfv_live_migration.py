#    Copyright 2016 Mirantis, Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import logging

import dpath.util
import pytest

from mos_tests.functions import common
from mos_tests.functions import file_cache
from mos_tests.functions import network_checks
from mos_tests.nfv.base import page_2mb
from mos_tests.nfv.base import TestBaseNFV
from mos_tests.nfv.conftest import computes_configuration
from mos_tests.nfv.conftest import get_cpu_distribition_per_numa_node
from mos_tests.settings import UBUNTU_QCOW2_URL

logger = logging.getLogger(__name__)


@pytest.fixture()
def nova_ceph(env):
    data = env.get_settings_data()
    if dpath.util.get(data, '*/storage/**/ephemeral_ceph/value'):
        pytest.skip("Nova Ceph RBD should be disabled")


@pytest.mark.undestructive
@pytest.mark.check_env_('is_vlan', 'is_ceph_enabled')
class TestLiveMigrationCeph(TestBaseNFV):

    flavors_to_create = [
        {'name': 'm1.medium.perfomance_1',
         'params': {'ram': 2048, 'vcpu': 2, 'disk': 20},
         'keys': {'aggregate_instance_extra_specs:pinned': 'true',
                  'hw:cpu_policy': 'dedicated', 'hw:numa_nodes': 1}},
        {'name': 'm1.medium.perfomance_2',
         'params': {'ram': 2048, 'vcpu': 2, 'disk': 20},
         'keys': {'aggregate_instance_extra_specs:pinned': 'true',
                  'hw:cpu_policy': 'dedicated', 'hw:numa_nodes': 2}}]

    @pytest.mark.parametrize('computes_with_hp_2mb',
                             [{'host_count': 2, 'hp_count_per_host': 768}],
                             indirect=['computes_with_hp_2mb'])
    @pytest.mark.testrail_id('838327')
    def test_lm_ceph_for_huge_pages(self, env, os_conn, nova_ceph,
                                    computes_with_hp_2mb, networks,
                                    volume, keypair, small_nfv_flavor,
                                    security_group):
        """This test checks that live migration executed successfully for
            instances created on computes with ceph and huge pages
            Steps:
            1. Create net1 with subnet, net2 with subnet and  router1 with
            interfaces to both nets
            2. Launch instance vm1 with volume vol1 on compute-1 in net1 with
            m1.small.hpgs
            3. Launch instance vm2 on compute-2 in net2 with m1.small.hpgs
            4. Make volume from vm2 volume_vm
            5. Launch instance vm3 on compute-2 in net2 with volume_vm
            with m1.small.hpgs
            6. Check vms connectivity
            7. Live migrate vm1 on compute-2 and check that vm moved to
            compute-2 with Active state
            8. Check vms connectivity
            9. Live migrate vm2 with block-migrate parameter on compute-1 and
            check that vm moved to compute-2 with Active state
            10. Check vms connectivity
            11. Live migrate vm3 on compute-1 and check that vm moved to
            compute-1 with Active state
            12. Check vms connectivity
        """
        count_to_allocate_2mb = small_nfv_flavor.ram * 1024 / page_2mb
        initial_conf = computes_configuration(env)
        hosts = computes_with_hp_2mb

        vm_0 = os_conn.create_server(
            name='vm1', flavor=small_nfv_flavor.id, key_name=keypair.name,
            nics=[{'net-id': networks[0]}],
            availability_zone='nova:{}'.format(hosts[0]),
            security_groups=[security_group.id],
            block_device_mapping={'vda': volume.id})
        vm_1 = os_conn.create_server(
            name='vm2', flavor=small_nfv_flavor.id, key_name=keypair.name,
            availability_zone='nova:{}'.format(hosts[1]),
            security_groups=[security_group.id],
            nics=[{'net-id': networks[1]}])
        volume_vm = self.create_volume_from_vm(os_conn, vm_1)
        vm_2 = os_conn.create_server(
            name='vm3', flavor=small_nfv_flavor.id, key_name=keypair.name,
            nics=[{'net-id': networks[1]}],
            availability_zone='nova:{}'.format(hosts[1]),
            security_groups=[security_group.id],
            block_device_mapping={'vda': volume_vm})
        vms = [vm_0, vm_1, vm_2]

        vms_distribution = [(hosts[0], 1), (hosts[1], 2)]
        current_conf = computes_configuration(env)
        for (host, nr_2mb) in vms_distribution:
            exp_free_2m = (initial_conf[host][page_2mb]['total'] -
                           nr_2mb * count_to_allocate_2mb)
            assert exp_free_2m == current_conf[host][page_2mb]['free']

        for vm in vms:
            self.check_instance_page_size(os_conn, vm, size=page_2mb)
        network_checks.check_vm_connectivity(env, os_conn)

        self.live_migrate(os_conn, vms[0], hosts[1], block_migration=False)
        vms_distribution = [(hosts[0], 0), (hosts[1], 3)]
        current_conf = computes_configuration(env)
        for (host, nr_2mb) in vms_distribution:
            exp_free_2m = (initial_conf[host][page_2mb]['total'] -
                           nr_2mb * count_to_allocate_2mb)
            assert exp_free_2m == current_conf[host][page_2mb]['free']
        network_checks.check_vm_connectivity(env, os_conn)

        self.live_migrate(os_conn, vms[1], hosts[0])
        vms_distribution = [(hosts[0], 1), (hosts[1], 2)]
        current_conf = computes_configuration(env)
        for (host, nr_2mb) in vms_distribution:
            exp_free_2m = (initial_conf[host][page_2mb]['total'] -
                           nr_2mb * count_to_allocate_2mb)
            assert exp_free_2m == current_conf[host][page_2mb]['free']
        network_checks.check_vm_connectivity(env, os_conn)

        self.live_migrate(os_conn, vms[2], hosts[0], block_migration=False)
        vms_distribution = [(hosts[0], 2), (hosts[1], 1)]
        current_conf = computes_configuration(env)
        for (host, nr_2mb) in vms_distribution:
            exp_free_2m = (initial_conf[host][page_2mb]['total'] -
                           nr_2mb * count_to_allocate_2mb)
            assert exp_free_2m == current_conf[host][page_2mb]['free']
        for vm in vms:
            self.check_instance_page_size(os_conn, vm, size=page_2mb)
        network_checks.check_vm_connectivity(env, os_conn)

    @pytest.mark.testrail_id('838329')
    def test_lm_ceph_for_cpu_pinning(self, env, os_conn, networks, nova_ceph,
                                     volume, flavors, security_group,
                                     aggregate):
        """This test checks that live migration executed successfully for
            instances created on computes with ceph and 2 Numa nodes
            Steps:
                1. Create net1 with subnet, net2 with subnet and  router1 with
                   interfaces to both nets
                2. Launch instance vm1 with volume vol1 on compute-1 in net1
                   with flavor m1.medium.performance_1
                3. Launch instance vm2 on compute-2 in net2 with flavor
                   m1.medium.performance_1
                4. Make volume from vm2 volume_vm2
                5. Launch instance vm3 on compute-2 in net2 with volume_vm2
                   with flavor m1.medium.performance_1
                6. Live migrate vm1 on compute-2 and check that vm moved to
                   compute-2 with Active state
                7. Live migrate vm2 with block-migrate parameter on compute-1
                   and check that vm moved to compute-1 with Active state
                8. Live migrate vm3 on compute-1 and check that vm moved to
                   compute-1 with Active state
                9. Check vms connectivity
                10. Remove vm1, vm2 and vm3
                11. Repeat actions for flavor m1.medium.performance_2
        """
        hosts = aggregate.hosts
        cpus = get_cpu_distribition_per_numa_node(env)

        for numa_count, cpu_flavor in enumerate(flavors, start=1):
            vm_1 = os_conn.create_server(
                name='vm1', flavor=cpu_flavor.id,
                nics=[{'net-id': networks[0]}],
                availability_zone='nova:{}'.format(hosts[0]),
                security_groups=[security_group.id],
                block_device_mapping={'vda': volume.id})
            vm_2 = os_conn.create_server(
                name='vm2', flavor=cpu_flavor.id,
                availability_zone='nova:{}'.format(hosts[1]),
                security_groups=[security_group.id],
                nics=[{'net-id': networks[1]}])
            volume_vm2 = self.create_volume_from_vm(os_conn, vm_2, size=20)
            vm_3 = os_conn.create_server(
                name='vm3', flavor=cpu_flavor.id,
                nics=[{'net-id': networks[1]}],
                availability_zone='nova:{}'.format(hosts[1]),
                security_groups=[security_group.id],
                block_device_mapping={'vda': volume_vm2})
            vms = [vm_1, vm_2, vm_3]
            network_checks.check_vm_connectivity(env, os_conn)

            self.live_migrate(os_conn, vms[0], hosts[1], block_migration=False)
            self.check_cpu_for_vm(os_conn, vms[0], numa_count, cpus[hosts[1]])

            self.live_migrate(os_conn, vms[1], hosts[0])
            self.check_cpu_for_vm(os_conn, vms[1], numa_count, cpus[hosts[0]])

            self.live_migrate(os_conn, vms[2], hosts[0], block_migration=False)
            self.check_cpu_for_vm(os_conn, vms[2], numa_count, cpus[hosts[0]])

            network_checks.check_vm_connectivity(env, os_conn)
            self.delete_servers(os_conn)


@pytest.mark.undestructive
@pytest.mark.check_env_('is_vlan', 'not is_ceph_enabled')
class TestLiveMigrationCinder(TestBaseNFV):

    small_nfv_flavor = {"name": "small.hpgs", "ram": 512, "vcpu": 1, "disk": 5}
    marker = 'cpulimit is installed'
    userdata = '\n'.join(['#!/bin/bash -v', 'apt-get install -y cpulimit',
                          'echo "{0}"'.format(marker)])

    flavors_to_create = [
        {'name': 'm1.medium.perfomance_1',
         'params': {'ram': 2048, 'vcpu': 2, 'disk': 20},
         'keys': {'aggregate_instance_extra_specs:pinned': 'true',
                  'hw:cpu_policy': 'dedicated', 'hw:numa_nodes': 1}},
        {'name': 'm1.medium.perfomance_2',
         'params': {'ram': 2048, 'vcpu': 2, 'disk': 20},
         'keys': {'aggregate_instance_extra_specs:pinned': 'true',
                  'hw:cpu_policy': 'dedicated', 'hw:numa_nodes': 2}}]

    @pytest.yield_fixture
    def ubuntu_image_id(self, os_conn):
        image = os_conn.glance.images.create(name="image_ubuntu",
                                             url=UBUNTU_QCOW2_URL,
                                             disk_format='qcow2',
                                             container_format='bare')
        with file_cache.get_file(UBUNTU_QCOW2_URL) as f:
            os_conn.glance.images.upload(image.id, f)
        yield image.id
        os_conn.glance.images.delete(image.id)

    def check_vm_connectivity_cirros_ubuntu(self, env, os_conn, keypair,
                                            cirros, ubuntu):
        ips = {cirros: os_conn.get_nova_instance_ips(cirros)['fixed'],
               ubuntu: os_conn.get_nova_instance_ips(ubuntu)['fixed']}
        network_checks.check_ping_from_vm(env, os_conn, cirros, timeout=None,
                                          ip_to_ping=ips[ubuntu])
        network_checks.check_ping_from_vm(env, os_conn, cirros, timeout=None)
        network_checks.check_ping_from_vm(env, os_conn, ubuntu,
                                          vm_keypair=keypair, timeout=None,
                                          ip_to_ping=ips[cirros],
                                          vm_login='ubuntu')
        network_checks.check_ping_from_vm(env, os_conn, ubuntu, timeout=None,
                                          vm_keypair=keypair,
                                          vm_login='ubuntu')

    @pytest.mark.parametrize('computes_with_hp_2mb',
                             [{'host_count': 2, 'hp_count_per_host': 512}],
                             indirect=['computes_with_hp_2mb'])
    @pytest.mark.testrail_id('838323')
    def test_lm_cinder_lvm_for_huge_pages(self, os_conn, env,
                                          computes_with_hp_2mb, networks,
                                          volume, small_nfv_flavor, keypair,
                                          security_group, ubuntu_image_id):
        """This test checks that live migration executed successfully for
            instances created on computes with cinder lvm and huge pages
            Steps:
            1. Create net1 with subnet, net2 with subnet and  router1 with
            interfaces to both nets
            2. Launch instance vm1 with volume vol1 on compute-1 in net1 with
            m1.small.hpgs
            3. Launch instance vm2 on compute-2 in net2 with m1.small.hpgs and
            ubuntu image
            4. Check vms connectivity
            5. Live migrate vm1 on compute-2 and check that vm moved to
            compute-2 with Active state
            6. Check vms connectivity
            7. Live migrate vm2 with block-migrate parameter on compute-1 and
            check that vm moved to compute-2 with Active state
            8. Check vms connectivity
            9. Run CPU load on vm2
            10. Live migrate vm2 on compute-2 with block-migrate parameter and
            check that vm moved to compute-2 with Active state
            11. Check vms connectivity
        """
        count_to_allocate_2mb = small_nfv_flavor.ram * 1024 / page_2mb
        initial_conf = computes_configuration(env)
        hosts = computes_with_hp_2mb

        vm_0 = os_conn.create_server(
            name='vm1', flavor=small_nfv_flavor.id,
            nics=[{'net-id': networks[0]}], key_name=keypair.name,
            availability_zone='nova:{}'.format(hosts[0]),
            security_groups=[security_group.id],
            block_device_mapping={'vda': volume.id})
        vm_1 = os_conn.create_server(
            name='vm2', image_id=ubuntu_image_id, flavor=small_nfv_flavor.id,
            key_name=keypair.name, userdata=self.userdata,
            availability_zone='nova:{}'.format(hosts[1]),
            security_groups=[security_group.id],
            nics=[{'net-id': networks[1]}])
        vms = [vm_0, vm_1]
        common.wait(lambda: self.marker in vm_1.get_console_output(),
                    timeout_seconds=5 * 60,
                    waiting_for="instance to be ready with cpulimit installed")

        expected_hosts_usage = [(hosts[0], 1), (hosts[1], 1)]
        current_conf = computes_configuration(env)
        for (host, nr_2mb) in expected_hosts_usage:
            exp_free_2m = (initial_conf[host][page_2mb]['total'] -
                           nr_2mb * count_to_allocate_2mb)
            assert exp_free_2m == current_conf[host][page_2mb]['free']
        for vm in vms:
            self.check_instance_page_size(os_conn, vm, size=page_2mb)

        self.check_vm_connectivity_cirros_ubuntu(
            env, os_conn, keypair, cirros=vms[0], ubuntu=vms[1])

        self.live_migrate(os_conn, vms[0], hosts[1], block_migration=False)
        expected_hosts_usage = [(hosts[0], 0), (hosts[1], 2)]
        current_conf = computes_configuration(env)
        for (host, nr_2mb) in expected_hosts_usage:
            exp_free_2m = (initial_conf[host][page_2mb]['total'] -
                           nr_2mb * count_to_allocate_2mb)
            assert exp_free_2m == current_conf[host][page_2mb]['free']

        self.live_migrate(os_conn, vms[1], hosts[0])
        expected_hosts_usage = [(hosts[0], 1), (hosts[1], 1)]
        current_conf = computes_configuration(env)
        for (host, nr_2mb) in expected_hosts_usage:
            exp_free_2m = (initial_conf[host][page_2mb]['total'] -
                           nr_2mb * count_to_allocate_2mb)
            assert exp_free_2m == current_conf[host][page_2mb]['free']

        self.check_vm_connectivity_cirros_ubuntu(
            env, os_conn, keypair, cirros=vms[0], ubuntu=vms[1])
        self.cpu_load(env, os_conn, vms[1], vm_keypair=keypair,
                      vm_login='ubuntu')

        self.live_migrate(os_conn, vms[1], hosts[1])
        expected_hosts_usage = [(hosts[0], 0), (hosts[1], 2)]
        current_conf = computes_configuration(env)
        for (host, nr_2mb) in expected_hosts_usage:
            exp_free_2m = (initial_conf[host][page_2mb]['total'] -
                           nr_2mb * count_to_allocate_2mb)
            assert exp_free_2m == current_conf[host][page_2mb]['free']
        for vm in vms:
            self.check_instance_page_size(os_conn, vm, size=page_2mb)
        self.check_vm_connectivity_cirros_ubuntu(
            env, os_conn, keypair, cirros=vms[0], ubuntu=vms[1])
        self.cpu_load(env, os_conn, vms[1], vm_keypair=keypair,
                      vm_login='ubuntu', action='stop')

    @pytest.mark.testrail_id('838325')
    def test_lm_cinder_lvm_for_cpu_pinning(self, env, os_conn, networks,
                                           volume, keypair, flavors,
                                           security_group, aggregate,
                                           ubuntu_image_id):
        """This test checks that live migration executed successfully for
            instances created on computes with cinder and 2 Numa nodes
            Steps:
                1. Create net1 with subnet, net2 with subnet and  router1 with
                   interfaces to both nets
                2. Launch instance vm1 with volume vol1 on compute-1 in net1
                   with flavor m1.small.performance_1
                3. Launch instance vm2 on compute-2 in net2 with with flavor
                   m1.small.performance_1
                4. Live migrate vm1 with block-migrate parameter on compute-2
                   and check that vm moved to compute-2 with Active state
                5. Live migrate vm2 with block-migrate parameter on compute-1
                   and check that vm moved to compute-1 with Active state
                6. Check vms connectivity
                7. Run CPU load on vm2
                8. Live migrate vm2 with block-migrate parameter on compute-2
                   and check that vm moved to compute-2 with Active state
                9. Check vms connectivity
                10. Remove vm1 and vm2
                11. Repeat actions for flavor m1.medium.performance_2
        """
        hosts = aggregate.hosts
        cpus = get_cpu_distribition_per_numa_node(env)

        for numa_count, cpu_flavor in enumerate(flavors, start=1):
            vm_1 = os_conn.create_server(
                name='vm1', flavor=cpu_flavor.id,
                nics=[{'net-id': networks[0]}], key_name=keypair.name,
                availability_zone='nova:{}'.format(hosts[0]),
                security_groups=[security_group.id],
                block_device_mapping={'vda': volume.id})
            vm_2 = os_conn.create_server(
                name='vm2', image_id=ubuntu_image_id,
                flavor=cpu_flavor.id,
                key_name=keypair.name, userdata=self.userdata,
                availability_zone='nova:{}'.format(hosts[1]),
                security_groups=[security_group.id],
                nics=[{'net-id': networks[1]}])
            vms = [vm_1, vm_2]
            common.wait(lambda: self.marker in vm_2.get_console_output(),
                        timeout_seconds=5 * 60,
                        waiting_for="cpulimit installed")
            self.check_vm_connectivity_cirros_ubuntu(
                env, os_conn, keypair, cirros=vms[0], ubuntu=vms[1])
            self.live_migrate(os_conn, vms[0], hosts[1], block_migration=False)
            self.live_migrate(os_conn, vms[1], hosts[0])
            self.check_vm_connectivity_cirros_ubuntu(
                env, os_conn, keypair, cirros=vms[0], ubuntu=vms[1])
            self.cpu_load(
                env, os_conn, vms[1], vm_keypair=keypair, vm_login='ubuntu')
            self.live_migrate(os_conn, vms[1], hosts[1])
            self.check_vm_connectivity_cirros_ubuntu(
                env, os_conn, keypair, cirros=vms[0], ubuntu=vms[1])
            self.check_cpu_for_vm(os_conn, vms[0], numa_count, cpus[hosts[1]])
            self.check_cpu_for_vm(os_conn, vms[1], numa_count, cpus[hosts[1]])
            self.delete_servers(os_conn)