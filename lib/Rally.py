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


class Rally(WorkloadBase.WorkloadBase):

    def __init__(self, config, hosts=None):
        self.logger = logging.getLogger('browbeat.Rally')
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

    def run_scenario(self, task_file, scenario_args, result_dir, test_name, benchmark):
        self.logger.debug("--------------------------------")
        self.logger.debug("task_file: {}".format(task_file))
        self.logger.debug("scenario_args: {}".format(scenario_args))
        self.logger.debug("result_dir: {}".format(result_dir))
        self.logger.debug("test_name: {}".format(test_name))
        self.logger.debug("--------------------------------")

        from_ts = int(time.time() * 1000)
        if 'sleep_before' in self.config['rally']:
            time.sleep(self.config['rally']['sleep_before'])
        task_args = str(scenario_args).replace("'", "\"")
        plugins = []
        if "plugins" in self.config['rally']:
            if len(self.config['rally']['plugins']) > 0:
                for plugin in self.config['rally']['plugins']:
                    for name in plugin:
                        plugins.append(plugin[name])
        plugin_string = ""
        if len(plugins) > 0:
            plugin_string = "--plugin-paths {}".format(",".join(plugins))
        cmd = "source {}; ".format(self.config['rally']['venv'])
        cmd += "rally {} task start {} --task-args \'{}\' 2>&1 | tee {}.log".format(
            plugin_string, task_file, task_args, test_name)
        from_time = time.time()
        self.tools.run_cmd(cmd)['stdout']
        to_time = time.time()
        if 'sleep_after' in self.config['rally']:
            time.sleep(self.config['rally']['sleep_after'])
        to_ts = int(time.time() * 1000)
        self.grafana.create_grafana_urls({'from_ts': from_ts, 'to_ts': to_ts})
        self.grafana.print_dashboard_url(test_name)
        self.grafana.log_snapshot_playbook_cmd(
            from_ts, to_ts, result_dir, test_name)
        self.grafana.run_playbook(from_ts, to_ts, result_dir, test_name)
        return (from_time, to_time)

    def update_tests(self):
        self.test_count += 1

    def update_pass_tests(self):
        self.pass_count += 1

    def update_fail_tests(self):
        self.error_count += 1

    def update_scenarios(self):
        self.scenario_count += 1

    def get_task_id(self, test_name):
        cmd = "grep \"rally task results\" {}.log | awk '{{print $4}}'".format(
            test_name)
        return self.tools.run_cmd(cmd)['stdout']

    def _get_details(self):
        self.logger.info(
            "Current number of Rally scenarios executed:{}".format(
                self.scenario_count))
        self.logger.info(
            "Current number of Rally tests executed:{}".format(self.test_count))
        self.logger.info(
            "Current number of Rally tests passed:{}".format(self.pass_count))
        self.logger.info(
            "Current number of Rally test failures:{}".format(self.error_count))

    def gen_scenario_html(self, task_ids, test_name):
        all_task_ids = ' '.join(task_ids)
        cmd = "source {}; ".format(self.config['rally']['venv'])
        cmd += "rally task report --task {} --out {}.html".format(
            all_task_ids, test_name)
        return self.tools.run_cmd(cmd)['stdout']

    def gen_scenario_json(self, task_id):
        cmd = "source {}; ".format(self.config['rally']['venv'])
        cmd += "rally task results {}".format(task_id)
        return self.tools.run_cmd(cmd)['stdout']

    def gen_scenario_json_file(self, task_id, test_name):
        cmd = "source {}; ".format(self.config['rally']['venv'])
        cmd += "rally task results {} > {}.json".format(task_id, test_name)
        return self.tools.run_cmd(cmd)['stdout']

    def rally_metadata(self, result, meta):
        result['rally_metadata'] = meta
        return result

    def file_to_json(self, filename, push_to_es=False):
        self.logger.info("Loading rally JSON file {} JSON".format(filename))
        rally_json = self.elastic.load_json_file(filename)
        errors, results = self.json_parse(rally_json)
        for error in errors:
            error_result = self.elastic.combine_metadata(error)
            with open("{}/{}-error_index-es.json".format(os.path.dirname(filename),
                                                         os.path.basename(filename)),
                      'w+') as error_file:
                json.dump(error_result, error_file)
        for result in results:
            result_doc = self.elastic.combine_metadata(result)
            with open("{}/{}-result_index-es.json".format(os.path.dirname(filename),
                                                          os.path.splitext(
                                                          os.path.basename(filename))[0]),
                      'w+') as result_file:
                json.dump(result_doc, result_file)
        return errors, results

    def json_parse(self, json_doc, metadata={}):
        """Function to extract data out of a json document

        Args:
            json_doc (json): json document to parse
            metadata (dict): dict containing run specific metadata, ie rally UUID.

        Returns:
            errors (list) : errors contained within the json_doc
            results (list) : results contained within the json_doc
        """
        rally_data = {}
        errors = []
        results = []
        if len(json_doc) < 1:
            self.logger.error("Issue with JSON document")
            return False
        es_ts = datetime.datetime.utcnow()
        for metrics in json_doc[0]['result']:
            for workload in metrics:
                if type(metrics[workload]) is dict:
                    for value in metrics[workload]:
                        if not type(metrics[workload][value]) is list:
                            if value not in rally_data:
                                rally_data[value] = []
                            rally_data[value].append(metrics[workload][value])
            if len(metrics['error']) > 0:
                iteration = 1
                workload_name = value
                if value.find('(') is not -1:
                    iteration = re.findall('\d+', value)[0]
                    workload_name = value.split('(')[0]
                error = {'action': workload_name.strip(),
                         'iteration': iteration,
                         'error_type': metrics['error'][0],
                         'error_msg': metrics['error'][1],
                         'timestamp': str(es_ts).replace(" ", "T"),
                         'rally_setup': json_doc[0]['key']
                         }
                if len(metadata) > 0:
                    error.update(metadata)
                errors.append(error)
        for workload in rally_data:
            if not type(rally_data[workload]) is dict:
                iteration = 1
                workload_name = workload
                if workload.find('(') is not -1:
                    iteration = re.findall('\d+', workload)[0]
                    workload_name = workload.split('(')[0]
                rally_stats = {'action': workload_name.strip(),
                               'iteration': iteration,
                               'timestamp': str(es_ts).replace(" ", "T"),
                               'grafana_url': [self.grafana.grafana_urls()],
                               'rally_setup': json_doc[0]['key'],
                               'raw': rally_data[workload]}
                if len(metadata) > 0:
                    rally_stats.update(metadata)
                results.append(rally_stats)
        return errors, results

    def json_result(self, task_id, scenario_name, run, test_name, result_dir):
        success = True
        self.logger.info("Loading Task_ID {} JSON".format(task_id))
        rally_json = self.elastic.load_json(self.gen_scenario_json(task_id))
        errors, results = self.json_parse(rally_json, {'scenario': scenario_name,
                                                       'browbeat_rerun': run,
                                                       'result': task_id})
        for error in errors:
            error_result = self.elastic.combine_metadata(error)
            status = self.elastic.index_result(error_result, test_name, result_dir,
                                               'rally', 'error')
            if not status:
                success = False
        for result in results:
            result = self.elastic.combine_metadata(result)
            status = self.elastic.index_result(
                result, test_name, result_dir, 'rally')
            if not status:
                success = False
        return success

    def start_workloads(self):
        """Iterates through all rally scenarios in browbeat yaml config file"""
        results = collections.OrderedDict()
        self.logger.info("Starting Rally workloads")
        es_ts = datetime.datetime.utcnow()
        dir_ts = es_ts.strftime("%Y%m%d-%H%M%S")
        self.logger.debug("Time Stamp (Prefix): {}".format(dir_ts))
        benchmarks = self.config.get('rally')['benchmarks']
        if (benchmarks is not None and len(benchmarks) > 0):
            for benchmark in benchmarks:
                if benchmark['enabled']:
                    self.logger.info("Benchmark: {}".format(benchmark['name']))
                    scenarios = benchmark['scenarios']
                    def_concurrencies = benchmark['concurrency']
                    def_times = benchmark['times']
                    self.logger.debug(
                        "Default Concurrencies: {}".format(def_concurrencies))
                    self.logger.debug("Default Times: {}".format(def_times))
                    for scenario in scenarios:
                        if scenario['enabled']:
                            self.update_scenarios()
                            self.update_total_scenarios()
                            scenario_name = scenario['name']
                            scenario_file = scenario['file']
                            self.logger.info(
                                "Running Scenario: {}".format(scenario_name))
                            self.logger.debug(
                                "Scenario File: {}".format(scenario_file))

                            del scenario['enabled']
                            del scenario['file']
                            del scenario['name']
                            if len(scenario) > 0:
                                self.logger.debug(
                                    "Overriding Scenario Args: {}".format(scenario))

                            result_dir = self.tools.create_results_dir(
                                self.config['browbeat']['results'], dir_ts,
                                self.__class__.__name__.lower(),
                                benchmark['name'], scenario_name
                            )
                            self.logger.debug(
                                "Created result directory: {}".format(result_dir))
                            workload = self.__class__.__name__
                            self.workload_logger(result_dir, workload)

                            # Override concurrency/times
                            if 'concurrency' in scenario:
                                concurrencies = scenario['concurrency']
                                del scenario['concurrency']
                            else:
                                concurrencies = def_concurrencies
                            concurrency_count_dict = collections.Counter(
                                concurrencies)
                            if 'times' not in scenario:
                                scenario['times'] = def_times

                            for concurrency in concurrencies:
                                scenario['concurrency'] = concurrency
                                for run in range(self.config['browbeat']['rerun']):
                                    if run not in results:
                                        results[run] = []
                                    self.update_tests()
                                    self.update_total_tests()
                                    if concurrency_count_dict[concurrency] == 1:
                                        test_name = "{}-browbeat-{}-{}-iteration-{}".format(
                                                    dir_ts, scenario_name, concurrency, run)
                                    else:
                                        test_name = "{}-browbeat-{}-{}-{}-iteration-{}".format(
                                                    dir_ts, scenario_name, concurrency,
                                                    concurrency_count_dict[concurrency], run)
                                        self.logger.debug("Duplicate concurrency {} found,"
                                                          " setting test name"
                                                          " to {}".format(concurrency, test_name))
                                        concurrency_count_dict[
                                            concurrency] -= 1

                                    if not result_dir:
                                        self.logger.error(
                                            "Failed to create result directory")
                                        exit(1)

                                    # Start connmon before rally
                                    if self.config['connmon']['enabled']:
                                        self.connmon.start_connmon()

                                    from_time, to_time = self.run_scenario(
                                        scenario_file, scenario, result_dir, test_name,
                                        benchmark['name'])

                                    # Stop connmon at end of rally task
                                    if self.config['connmon']['enabled']:
                                        self.connmon.stop_connmon()
                                        try:
                                            self.connmon.move_connmon_results(
                                                result_dir, test_name)
                                        except Exception:
                                            self.logger.error(
                                                "Connmon Result data missing, \
                                                Connmon never started")
                                            return False
                                        self.connmon.connmon_graphs(
                                            result_dir, test_name)
                                    new_test_name = test_name.split('-')
                                    new_test_name = new_test_name[3:]
                                    new_test_name = "-".join(new_test_name)

                                    # Find task id (if task succeeded in
                                    # running)
                                    task_id = self.get_task_id(test_name)
                                    if task_id:
                                        self.logger.info(
                                            "Generating Rally HTML for task_id : {}".
                                            format(task_id))
                                        self.gen_scenario_html(
                                            [task_id], test_name)
                                        self.gen_scenario_json_file(
                                            task_id, test_name)
                                        results[run].append(task_id)
                                        self.update_pass_tests()
                                        self.update_total_pass_tests()
                                        if self.config['elasticsearch']['enabled']:
                                            # Start indexing
                                            index_status = self.json_result(
                                                task_id, scenario_name, run, test_name, result_dir)
                                            if not index_status:
                                                self.update_index_failures()
                                            self.get_time_dict(to_time, from_time,
                                                               benchmark[
                                                                   'name'], new_test_name,
                                                               workload, "pass", index_status)
                                        else:
                                            self.get_time_dict(to_time, from_time, benchmark[
                                                               'name'], new_test_name,
                                                               workload, "pass", )

                                    else:
                                        self.logger.error(
                                            "Cannot find task_id")
                                        self.update_fail_tests()
                                        self.update_total_fail_tests()
                                        self.get_time_dict(
                                            to_time, from_time, benchmark[
                                                'name'], new_test_name,
                                            workload, "fail")

                                    for data in glob.glob("./{}*".format(test_name)):
                                        shutil.move(data, result_dir)

                                    self._get_details()

                        else:
                            self.logger.info(
                                "Skipping {} scenario enabled: false".format(scenario['name']))
                else:
                    self.logger.info(
                        "Skipping {} benchmarks enabled: false".format(benchmark['name']))
            self.logger.debug("Creating Combined Rally Reports")
            for run in results:
                combined_html_name = 'all-rally-run-{}'.format(run)
                self.gen_scenario_html(results[run], combined_html_name)
                if os.path.isfile('{}.html'.format(combined_html_name)):
                    shutil.move('{}.html'.format(combined_html_name),
                                '{}/{}'.format(self.config['browbeat']['results'], dir_ts))
        else:
            self.logger.error("Config file contains no rally benchmarks.")
