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


import logging
import os
import time

import pytest

from mos_tests.functions.base import OpenStackTestCase
from mos_tests.functions import common as common_functions
from mos_tests import settings

logger = logging.getLogger(__name__)


@pytest.mark.undestructive
class WindowCompatibilityIntegrationTests(OpenStackTestCase):
    """Basic automated tests for OpenStack Windows Compatibility verification.
    """

    def is_instance_ready(self, instance):
        """Determine instance is ready by mean brightness of screenshot

        Minimal registered level for booted machine was 33, so 25 is used as
        threshold value.
        """
        hypervisor_hostname = getattr(self.instance,
                                      'OS-EXT-SRV-ATTR:hypervisor_hostname')
        instance_name = getattr(self.instance, 'OS-EXT-SRV-ATTR:instance_name')
        compute_node = self.env.find_node_by_fqdn(hypervisor_hostname)
        screenshot_path = '/tmp/instance_screenshot.ppm'

        with compute_node.ssh() as remote:
            remote.check_call(
                'virsh send-key {name} --codeset win32 VK_TAB'.format(
                    name=instance_name))
            remote.check_call(
                'virsh screenshot {name} --file {path}'.format(
                    name=instance_name, path=screenshot_path),
                verbose=False)
            with remote.open(screenshot_path, 'rb') as f:
                data = f.read()
        return sum(ord(x) for x in data) / len(data) > 25

    def wait_instance_to_boot(self):
        common_functions.wait(
            lambda: self.is_instance_ready(self.instance),
            timeout_seconds=60 * 60,
            sleep_seconds=60,
            waiting_for='windows instance to boot')

    def setUp(self):
        super(self.__class__, self).setUp()

        # Get path on node to 'images' dir
        self.image_name = os.path.join(settings.TEST_IMAGE_PATH,
                                       settings.WIN_SERVER_QCOW2)

        self.uid_list = []

        # timeouts (in minutes)
        self.ping_timeout = 3
        self.hypervisor_timeout = 10

        self.amount_of_images_before = len(list(self.glance.images.list()))
        self.image = None
        self.expected_flavor_id = self.nova.flavors.find(name='m1.small').id
        self.instance = None
        self.security_group_name = "ms_compatibility"
        # protect for multiple definition of the same group
        for sg in self.nova.security_groups.list():
            if sg.name == self.security_group_name:
                self.nova.security_groups.delete(sg)
        # adding required security group
        self.the_security_group = self.nova.security_groups.create(
            name=self.security_group_name,
            description="Windows Compatibility")
        # Add rules for ICMP, TCP/22
        self.icmp_rule = self.nova.security_group_rules.create(
            self.the_security_group.id,
            ip_protocol="icmp",
            from_port=-1,
            to_port=-1,
            cidr="0.0.0.0/0")
        self.tcp_rule = self.nova.security_group_rules.create(
            self.the_security_group.id,
            ip_protocol="tcp",
            from_port=22,
            to_port=22,
            cidr="0.0.0.0/0")
        # Add both rules to default group
        self.default_security_group_id = 0
        for sg in self.nova.security_groups.list():
            if sg.name == 'default':
                self.default_security_group_id = sg.id
                break
        self.icmp_rule_default = self.nova.security_group_rules.create(
            self.default_security_group_id,
            ip_protocol="icmp",
            from_port=-1,
            to_port=-1,
            cidr="0.0.0.0/0")
        self.tcp_rule_default = self.nova.security_group_rules.create(
            self.default_security_group_id,
            ip_protocol="tcp",
            from_port=22,
            to_port=22,
            cidr="0.0.0.0/0")
        # adding floating ip
        self.floating_ip = self.nova.floating_ips.create(
            self.nova.floating_ip_pools.list()[0].name)

        # creating of the image
        self.image = self.glance.images.create(
            name='MyTestSystem',
            disk_format='qcow2',
            container_format='bare')
        with open(self.image_name, 'rb') as win_image_file:
            self.glance.images.upload(
                self.image.id,
                win_image_file)
        # check that required image in active state
        is_activated = False
        while not is_activated:
            for image_object in self.glance.images.list():
                if image_object.id == self.image.id:
                    self.image = image_object
                    logger.info(
                        "Image in the {} state".format(self.image.status))
                    if self.image.status == 'active':
                        is_activated = True
                        break
            time.sleep(1)

        # Default - the first
        network_id = self.nova.networks.list()[0].id
        # More detailed check of network list
        for network in self.nova.networks.list():
            if 'internal' in network.label:
                network_id = network.id
        logger.info("Starting with network interface id {}".format(network_id))

        logger.info("Starting with flavor {}".format(
            self.nova.flavors.get(self.expected_flavor_id)))
        # nova boot
        self.instance = common_functions.create_instance(
            nova_client=self.nova,
            inst_name="MyTestSystemWithNova",
            flavor_id=self.expected_flavor_id,
            net_id=network_id,
            security_groups=[self.the_security_group.name, 'default'],
            image_id=self.image.id)

        logger.info("Using following floating ip {}".format(
            self.floating_ip.ip))

        self.instance.add_floating_ip(self.floating_ip)

        self.assertTrue(common_functions.check_ip(self.nova,
                                                  self.instance.id,
                                                  self.floating_ip.ip))

        self.wait_instance_to_boot()

    def tearDown(self):
        if self.instance is not None:
            common_functions.delete_instance(self.nova, self.instance.id)
        if self.image is not None:
            common_functions.delete_image(self.glance, self.image.id)
        # delete the floating ip
        self.nova.floating_ips.delete(self.floating_ip)
        # delete the security group
        self.nova.security_group_rules.delete(self.icmp_rule)
        self.nova.security_group_rules.delete(self.tcp_rule)
        self.nova.security_groups.delete(self.the_security_group.id)
        # delete security rules from the 'default' group
        self.nova.security_group_rules.delete(self.icmp_rule_default)
        self.nova.security_group_rules.delete(self.tcp_rule_default)
        self.assertEqual(self.amount_of_images_before,
                         len(list(self.glance.images.list())),
                         "Length of list with images should be the same")

    @pytest.mark.testrail_id('634680')
    def test_create_instance_with_windows_image(self):
        """This test checks that instance with Windows image could be created

        Steps:
        1. Upload Windows 2012 Server image to Glance
        2. Create VM with this Windows image
        3. Assign floating IP to this VM
        4. Ping this VM and verify that we can ping it
        :return: Nothing
        """
        ping_result = common_functions.ping_command(self.floating_ip.ip)
        self.assertTrue(ping_result, "Instance is not reachable")

    @pytest.mark.testrail_id('634681')
    def test_pause_and_unpause_instance_with_windows_image(self):
        """This test checks that instance with Windows image could be paused
        and unpaused

        Steps:
        1. Upload Windows 2012 Server image to Glance
        2. Create VM with this Windows image
        3. Assign floating IP to this VM
        4. Ping this VM and verify that we can ping it
        5. Pause this VM
        6. Verify that we can't ping it
        7. Unpause it and verify that we can ping it again
        8. Reboot VM
        9. Verify that we can ping this VM after reboot.
        :return: Nothing
        """
        # Initial check
        ping_result = common_functions.ping_command(self.floating_ip.ip)
        self.assertTrue(ping_result, "Instance is not reachable")
        # Paused state check
        self.instance.pause()
        # Make sure that the VM in 'Paused' state
        ping_result = common_functions.ping_command(self.floating_ip.ip,
                                                    should_be_available=False)
        self.assertTrue(ping_result, "Instance is reachable")
        # Unpaused state check
        self.instance.unpause()
        # Make sure that the VM in 'Unpaused' state
        ping_result = common_functions.ping_command(self.floating_ip.ip)
        self.assertTrue(ping_result, "Instance is not reachable")

        # Reboot the VM and make sure that we can ping it
        self.instance.reboot(reboot_type='HARD')
        instance_status = common_functions.check_inst_status(
            self.nova,
            self.instance.id,
            'ACTIVE')
        self.instance = [s for s in self.nova.servers.list()
                         if s.id == self.instance.id][0]
        if not instance_status:
            raise AssertionError(
                "Instance status is '{0}' instead of 'ACTIVE".format(
                    self.instance.status))

        self.wait_instance_to_boot()

        # Waiting for up-and-run of Virtual Machine after reboot
        ping_result = common_functions.ping_command(self.floating_ip.ip)
        self.assertTrue(ping_result, "Instance is not reachable")

    @pytest.mark.testrail_id('638381')
    def test_suspend_and_resume_instance_with_windows_image(self):
        """This test checks that instance with Windows image can be suspended
        and resumed

        Steps:
        1. Upload Windows 2012 Server image to Glance
        2. Create VM with this Windows image
        3. Assign floating IP to this VM
        4. Ping this VM and verify that we can ping it
        5. Suspend VM
        6. Verify that we can't ping it
        7. Resume and verify that we can ping it again.
        8. Reboot VM
        9. Verify that we can ping this VM after reboot.
        :return: Nothing
        """
        # Initial check
        ping_result = common_functions.ping_command(self.floating_ip.ip)
        self.assertTrue(ping_result, "Instance is not reachable")
        # Suspend state check
        self.instance.suspend()
        # Make sure that the VM in 'Suspended' state
        ping_result = common_functions.ping_command(
            self.floating_ip.ip,
            should_be_available=False
        )
        self.assertTrue(ping_result, "Instance is reachable")
        # Resume state check
        self.instance.resume()
        # Make sure that the VM in 'Resume' state
        ping_result = common_functions.ping_command(self.floating_ip.ip)
        self.assertTrue(ping_result, "Instance is not reachable")

        # Reboot the VM and make sure that we can ping it
        self.instance.reboot(reboot_type='HARD')
        instance_status = common_functions.check_inst_status(
            self.nova,
            self.instance.id,
            'ACTIVE')
        self.instance = [s for s in self.nova.servers.list()
                         if s.id == self.instance.id][0]
        if not instance_status:
            raise AssertionError(
                "Instance status is '{0}' instead of 'ACTIVE".format(
                    self.instance.status))

        self.wait_instance_to_boot()

        # Waiting for up-and-run of Virtual Machine after reboot
        ping_result = common_functions.ping_command(self.floating_ip.ip)
        self.assertTrue(ping_result, "Instance is not reachable")

    @pytest.mark.testrail_id('634682')
    def test_live_migration_for_windows_instance(self):
        """This test checks that instance with Windows Image could be
        migrated without any issues

        Steps:
        1. Upload Windows 2012 Server image to Glance
        2. Create VM with this Windows image
        3. Assign floating IP to this VM
        4. Ping this VM and verify that we can ping it
        5. Migrate this VM to another compute node
        6. Verify that live Migration works fine for Windows VMs
        and we can successfully ping this VM
        7. Reboot VM and verify that
        we can successfully ping this VM after reboot.

        :return: Nothing
        """
        # 1. 2. 3. -> Into setUp function
        # 4. Ping this VM and verify that we can ping it
        hypervisor_hostname_attribute = "OS-EXT-SRV-ATTR:hypervisor_hostname"
        ping_result = common_functions.ping_command(self.floating_ip.ip)
        self.assertTrue(ping_result, "Instance is not reachable")
        hypervisors = {h.hypervisor_hostname: h for h
                       in self.nova.hypervisors.list()}
        old_hyper = getattr(self.instance,
                            hypervisor_hostname_attribute)
        logger.info("Old hypervisor is: {}".format(old_hyper))
        new_hyper = [h for h in hypervisors.keys() if h != old_hyper][0]
        logger.info("New hypervisor is: {}".format(new_hyper))
        # Execute the live migrate
        self.instance.live_migrate(new_hyper, block_migration=True)

        self.instance = self.nova.servers.get(self.instance.id)
        end_time = time.time() + 60 * self.hypervisor_timeout
        debug_string = "Waiting for changes."
        while getattr(self.instance,
                      hypervisor_hostname_attribute) != new_hyper:
            if time.time() > end_time:
                # it can fail because of this issue
                # https://bugs.launchpad.net/mos/+bug/1544564
                logger.info(debug_string)
                raise AssertionError(
                    "Hypervisor is not changed after live migration")
            time.sleep(30)
            debug_string += "."
            self.instance = self.nova.servers.get(self.instance.id)
        logger.info(debug_string)
        self.assertEqual(self.instance.status, 'ACTIVE')
        # Ping the Virtual Machine
        ping_result = common_functions.ping_command(self.floating_ip.ip)
        self.assertTrue(ping_result, "Instance is not reachable")

        # Reboot the VM and make sure that we can ping it
        self.instance.reboot(reboot_type='HARD')
        instance_status = common_functions.check_inst_status(
            self.nova,
            self.instance.id,
            'ACTIVE')
        self.instance = [s for s in self.nova.servers.list()
                         if s.id == self.instance.id][0]
        if not instance_status:
            raise AssertionError(
                "Instance status is '{0}' instead of 'ACTIVE".format(
                    self.instance.status))

        self.wait_instance_to_boot()

        # Waiting for up-and-run of Virtual Machine after reboot
        ping_result = common_functions.ping_command(self.floating_ip.ip)
        self.assertTrue(ping_result, "Instance is not reachable")
