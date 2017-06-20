#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

from rally.task import scenario
from rally.plugins.openstack.scenarios.vm import utils as vm_utils
from rally.plugins.openstack.scenarios.neutron import utils as neutron_utils
from rally.task import types
from rally.task import validation
from rally.common import sshutils
import time
import StringIO
import csv
import json
import datetime
import logging
from Elastic import Elastic
import subprocess, os

LOG = logging.getLogger(__name__)

class BrowbeatPlugin(neutron_utils.NeutronScenario,
                     vm_utils.VMScenario,
                     scenario.Scenario):
    @validation.required_openstack(users=True)
    @scenario.configure(context={})
    def nfv(
            self,
            instances=1,
            rcfile="/home/stack/overcloudrc",
            vm_image_name="nfv",
            vm_image_file="/home/stack/nfv.qcow2",
            traffic_gen_src_slot,
            traffic_gen_dst_slot,
            traffic_bidirectional=1,
            traffic_loss_pct=0.002,
            data_vlan_start=100,
            routing=vpp,
            data_pkt_size=64,
            run_pbench_trafficgen=true
            repin_ovs_nonpmd=false,
            repin_ovs_pmd=true,
            repin_kvm_emulator=true,
            pmd_vm_eth0,
            pmd_vm_eth1,
            pmd_vm_eth2,
            pmd_dpdk0,
            pmd_dpdk1,
            pmd_dpdk2,
            **kwargs):

        script_dir = os.path.dirname(os.path.realpath(__file__))
        bash_script = os.path.join(script_dir, "prepare.sh")
        if not (os.path.isfile(bash_script) and os.access(bash_script, os.X_OK)):
           LOG.error("no executable shell script found")
           return 1
  
        bash_env = os.environ.copy()
        bash_env["rcfile"] = rcfile 
        bash_env["num_vm"] = instances
        bash_env["vm_image_name"] = vm_image_name
        bash_env["vm_image_file"] = vm_image_file
        bash_env["traffic_gen_src_slot"] = traffic_gen_src_slot
        bash_env["traffic_gen_dst_slot"] = traffic_gen_dst_slot
        bash_env["traffic_bidirectional"] =  traffic_bidirectional
        bash_env["traffic_loss_pct"] = traffic_loss_pct
        bash_env["data_vlan_start"] = data_vlan_start
        bash_env["data_pkt_size"] = data_pkt_size
        bash_env["repin_ovs_nonpmd"] = repin_ovs_nonpmd
        bash_env["repin_kvm_emulator"] = repin_kvm_emulator
        bash_env["repin_ovs_pmd"] = repin_ovs_pmd
        bash_env["pmd_vm_eth0"] = pmd_vm_eth0
        bash_env["pmd_vm_eth1"] = pmd_vm_eth1
        bash_env["pmd_vm_eth2"] = pmd_vm_eth2
        bash_env["pmd_dpdk0"] = pmd_dpdk0
        bash_env["pmd_dpdk1"] = pmd_dpdk1
        bash_env["pmd_dpdk2"] = pmd_dpdk2
        bash_env["routing"] = routing
        bash_env["enable_multi_queue"] = enable_multi_queue
        bash_env["run_pbench_trafficgen'] = run_pbench_trafficgen

        proc = subprocess.Popen(bash_script, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True, env=bash_env)
        out, err = proc.communicate()
        #LOG.info( out)
 
