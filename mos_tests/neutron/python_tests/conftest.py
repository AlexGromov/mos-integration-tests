#    Copyright 2015 Mirantis, Inc.
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

import pytest
from distutils.spawn import find_executable
from devops.helpers.helpers import wait

from mos_tests.environment.devops_client import DevopsClient
from mos_tests.environment.fuel_client import FuelClient
from mos_tests.environment.os_actions import OpenStackActions
from mos_tests.settings import SERVER_ADDRESS
from mos_tests.settings import KEYSTONE_USER
from mos_tests.settings import KEYSTONE_PASS
from mos_tests.settings import SSH_CREDENTIALS


@pytest.fixture
def env_name(request):
    return request.config.getoption("--env")


@pytest.fixture
def snapshot_name(request):
    return request.config.getoption("--snapshot")


@pytest.fixture
def revert_snapshot(env_name, snapshot_name):
    """Revert Fuel devops snapshot before test"""
    DevopsClient.revert_snapshot(env_name=env_name,
                                 snapshot_name=snapshot_name)


@pytest.fixture
def fuel_master_ip(request, env_name, snapshot_name):
    """Get fuel master ip"""
    fuel_ip = request.config.getoption("--fuel-ip")
    if not fuel_ip:
        fuel_ip = DevopsClient.get_admin_node_ip(env_name=env_name)
        revert_snapshot(env_name, snapshot_name)
        setattr(request.node, 'reverted', True)
    if not fuel_ip:
        fuel_ip = SERVER_ADDRESS
    return fuel_ip


@pytest.fixture
def fuel(fuel_master_ip):
    """Initialized fuel client"""
    return FuelClient(ip=fuel_master_ip,
                      login=KEYSTONE_USER,
                      password=KEYSTONE_PASS,
                      ssh_login=SSH_CREDENTIALS['login'],
                      ssh_password=SSH_CREDENTIALS['password'])


@pytest.fixture
def env(fuel):
    """Environment instance"""
    return fuel.get_last_created_cluster()


@pytest.fixture
def os_conn(env):
    """Openstack common actions"""
    os_conn = OpenStackActions(
        controller_ip=env.get_primary_controller_ip(),
        cert=env.certificate)

    def is_alive():
        try:
            os_conn.get_servers()
            return True
        except Exception:
            return False
    wait(is_alive, timeout=60 * 5, timeout_msg="OpenStack nova isn't alive")
    return os_conn


@pytest.yield_fixture
def clear_l3_ban(env, os_conn):
    """Clear all l3-agent bans after test"""
    yield
    controllers = env.get_nodes_by_role('controller')
    ip = controllers[0].data['ip']
    with env.get_ssh_to_node(ip) as remote:
        for node in controllers:
            remote.execute("pcs resource clear p_neutron-l3-agent {0}".format(
                node.data['fqdn']))


@pytest.fixture
def clean_os(os_conn):
    """Cleanup OpenStack"""
    os_conn.cleanup_network()


@pytest.yield_fixture(scope="function")
def setup(request, env_name, snapshot_name, env, os_conn):
    if not getattr(request.node, 'reverted', False) and env_name:
        revert_snapshot(env_name, snapshot_name)
    yield
    if not env_name:
        clear_l3_ban(env, os_conn)
        clean_os(os_conn)


@pytest.fixture
def tshark():
    """Returns tshark bin path"""
    path = find_executable('tshark')
    if path is None:
        pytest.skip('requires tshark executable')
    return path


@pytest.fixture
def check_ha_env(env):
    """Check that deployment type is HA"""
    if not env.is_ha:
        pytest.skip('requires HA cluster')


@pytest.fixture
def check_several_computes(env):
    """Check that count of compute nodes not less than 2"""
    if len(env.get_nodes_by_role('compute')) < 2:
        pytest.skip('requires at least 2 compute node')


@pytest.fixture
def check_devops(env_name):
    """Check that devops env is defined"""
    try:
        DevopsClient.get_env(env_name=env_name)
    except Exception:
        pytest.skip('requires devops env to be defined')


@pytest.fixture
def check_vxlan(env):
    """Check that env has vxlan network segmentation"""
    if env.network_segmentation_type != 'tun':
        pytest.skip('requires vxlan segmentation')
