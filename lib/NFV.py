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

import collections
import Connmon
import datetime
import Elastic
import glob
import Grafana
import logging
import os
import re
import shutil
import time
import Tools
import WorkloadBase
import json
import subprocess

class NFV(WorkloadBase.WorkloadBase):

    def __init__(self, config):
        self.logger = logging.getLogger('browbeat.NFV')
        self.config = config
        self.tools = Tools.Tools(self.config)
        self.connmon = Connmon.Connmon(self.config)
        self.grafana = Grafana.Grafana(self.config)
        self.elastic = Elastic.Elastic(
            self.config, self.__class__.__name__.lower())
        self.error_count = 0
        self.pass_count = 0
        self.test_count = 0
        self.scenario_count = 0

    def _log_details(self):
        self.logger.info(
            "Current number of NFV scenarios executed: {}".format(self.scenario_count))
        self.logger.info(
            "Current number of NFV test(s) executed: {}".format(self.test_count))
        self.logger.info(
            "Current number of NFV test(s) succeeded: {}".format(self.pass_count))
        self.logger.info(
            "Current number of NFV test failures: {}".format(self.error_count))


    def string_to_dict(self, string):
        """Function for converting "|" quoted hash data into python dictionary."""
        dict_data = {}
        split_data = string.split('|,|')
        split_data[0] = split_data[0][1:]
        split_data[-1] = split_data[-1][:-1]
        for item in split_data:
            split_item = item.replace('.', '_').split(':', 1)
            dict_data[split_item[0]] = ast.literal_eval("'" + split_item[1] + "'")
        return dict_data

    def run_benchmark(self, benchmark_config, result_dir, test_name):
        self.logger.debug("--------------------------------")
        self.logger.debug("Benchmark_config: {}".format(benchmark_config))
        self.logger.debug("result_dir: {}".format(result_dir))
        self.logger.debug("test_name: {}".format(test_name))
        self.logger.debug("--------------------------------")

        # clean up the results folder before start
        #for nfv_result_dir glob.glob("/var/lib/pbench-agent/*"):
        #    shutil.rmtree(nfv_result_dir)

        if 'enabled' in benchmark_config:
            del benchmark_config['enabled']
        cmd = "{}/prepare.sh".format(self.config['nfv']['dir'])
        nfv_env = ""
        for parameter, value in benchmark_config.iteritems():
            nfv_env += "{}={} ".format(parameter, value)
        bash_env = os.environ.copy()
        bash_env["browbeat_nfv_vars"] = nfv_env
        stdout_file = open("{}/nfv.stdout.log".format(result_dir), 'w')
        stderr_file = open("{}/nfv.stderr.log".format(result_dir), 'w')
        from_ts = time.time() 
        if 'sleep_before' in self.config['nfv']:
            time.sleep(self.config['nfv']['sleep_before'])

        proc = subprocess.Popen(cmd, stdout=stdout_file, 
                                stderr=stderr_file, shell=True, env=bash_env) 
        proc.communicate()

        if 'sleep_after' in self.config['nfv']:
            time.sleep(self.config['nfv']['sleep_after'])
        to_ts = time.time()

        if self.config['connmon']['enabled']:
            self.connmon.stop_connmon()
            try:
                self.connmon.move_connmon_results(result_dir, test_name)
                self.connmon.connmon_graphs(result_dir, test_name)
            except Exception:
                self.logger.error(
                    "Connmon Result data missing, Connmon never started")

        # Determine success
        success = True
        try:
            with open("{}/nfv.stdout.log".format(result_dir), 'r') as stdout:
                if any('FAIL' in line for line in stdout):
                    self.logger.error("Benchmark failed..")
                    success = False 
                else:
                    self.logger.info("Benchmark completed.")
        except IOError:
            self.logger.error(
                "File missing: {}/nfv.stdout.log".format(result_dir))

        # Copy all results
        for nfv_result_dir in glob.glob("/var/lib/pbench-agent/{}*".format(benchmark_config['pbench_report_prefix'])):
            shutil.copy("{}/result.json".format(nfv_result_dir), result_dir)
            shutil.copy("{}/pbench-trafficgen.cmd".format(nfv_result_dir), result_dir)
            subprocess.call(["/usr/bin/sudo", "rm", "-rf", nfv_result_dir])
            #shutil.rmtree(nfv_result_dir)

        return (success, from_ts, to_ts)

    def update_tests(self):
        self.test_count += 1

    def update_pass_tests(self):
        self.pass_count += 1

    def update_fail_tests(self):
        self.error_count += 1

    def update_scenarios(self):
        self.scenario_count += 1

    def get_error_details(self, result_dir):
        error_details = []
        with open('{}/nfv.stderr.log'.format(result_dir)) as nfv_stderr:
            for line in nfv_stderr:
                if 'ERR' in line or 'Err' in line or 'Exception' in line:
                    error_details.append(line)
        return error_details


    def index_results(self, sucessful_run, result_dir, test_name, browbeat_rerun, benchmark_config):
        es_ts = datetime.datetime.utcnow()
        index_success = True
        if sucessful_run:
            nfv_results = self.elastic.load_json_file(
                                        '{}/result.json'.format(result_dir))
            for iteration in nfv_results:
                complete_result_json = {'browbeat_scenario': benchmark_config}
                complete_result_json['browbeat_rerun'] = browbeat_rerun
                complete_result_json['timestamp'] = str(es_ts).replace(" ", "T")
                complete_result_json['grafana_url'] = self.grafana.grafana_urls()
                complete_result_json['nfv_setup'] = iteration["iteration_data"]["parameters"]["benchmark"]
                complete_result_json["throughput"] = iteration["iteration_data"]["throughput"]
                
                result = self.elastic.combine_metadata(complete_result_json)
                if not self.elastic.index_result(result, test_name, result_dir,
                                                 str(result_count), 'result'):
                    index_success = False
                    self.update_index_failures()
        else:
            complete_result_json = {'browbeat_scenario': benchmark_config}
            complete_result_json['nfv_errors'] = self.get_error_details(result_dir)
            complete_result_json['browbeat_rerun'] = browbeat_rerun
            complete_result_json['timestamp'] = str(es_ts).replace(" ", "T")
            complete_result_json['grafana_url'] = self.grafana.grafana_urls()
            result = self.elastic.combine_metadata(complete_result_json)
            index_success = self.elastic.index_result(result, test_name, result_dir, _type='error')
        return index_success


    def start_workloads(self):
        """Iterates through all nfv scenarios in browbeat yaml config file"""
        self.logger.info("Starting NFV workloads")
        time_stamp = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S") 
        self.logger.debug("Time Stamp (Prefix): {}".format(time_stamp)) 
        benchmarks = self.config.get('nfv')['benchmarks']
        if (benchmarks is not None and len(benchmarks) > 0):
            for benchmark in benchmarks:
                if benchmark['enabled']:
                    self.logger.info("Benchmark: {}".format(benchmark['name']))
                    self.update_scenarios()
                    self.update_total_scenarios()
                    for run in range(self.config['browbeat']['rerun']):
                        self.update_tests()
                        self.update_total_tests()
                        result_dir = self.tools.create_results_dir(
                            self.config['browbeat']['results'], time_stamp, benchmark['name'],
                            str(run))
                        test_name = "{}-{}-{}".format(time_stamp, benchmark['name'], run)
                        workload = self.__class__.__name__
                        self.workload_logger(result_dir, workload)
                        success, from_ts, to_ts = self.run_benchmark(benchmark, result_dir, test_name)
                        index_success = 'disabled'
                        if self.config['elasticsearch']['enabled']:
                            index_success = self.index_results(success, result_dir, test_name, run,
                                                               benchmark)
                        new_test_name = test_name.split('-')
                        new_test_name = new_test_name[2:]
                        new_test_name = '-'.join(new_test_name)
                        if success:
                            self.update_pass_tests()
                            self.update_total_pass_tests()
                            self.get_time_dict(to_ts, from_ts, benchmark['name'],
                                               new_test_name, self.__class__.__name__, "pass",
                                               index_success)
                        else:
                            self.update_fail_tests()
                            self.update_total_fail_tests()
                            self.get_time_dict(to_ts, from_ts, benchmark['name'],
                                               new_test_name, self.__class__.__name__, "fail",
                                               index_success)
                        self._log_details()
                else:
                    self.logger.info(
                        "Skipping {} benchmark, enabled: false".format(benchmark['name']))
        else:
            self.logger.error("Config file contains no nfv benchmarks.")
