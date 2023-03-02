#!/usr/bin/env python3

# example query
# max((rate(container_cpu_usage_seconds_total{namespace="pimpay-master",container!="",container!~"job|nginx|php-logs|POD|main|.*-redis.*"}[1d]))) by (pod)
# sum by (pod)(avg_over_time(rate(container_cpu_usage_seconds_total{namespace="pimpay-master",container!="",container!~"job|nginx|php-logs|POD|main|.*-redis.*"}[1m])[1d:1m])) * 1000
# sum by (pod)(quantile_over_time(0.95,rate(container_cpu_usage_seconds_total{namespace="pimpay-master",container!="",container!~"job|nginx|php-logs|POD|main|.*-redis.*"}[1m])[1d:1m])) * 1000
# max(max_over_time(container_memory_max_usage_bytes{namespace="pimpay-master",container!="",container!~"job|nginx|php-logs|POD|main|.*-redis.*"} [1d:5m]))by (pod) /(1024* 1024)

import requests
import os
import sys
import json
import jinja2
import yaml
from argparse import ArgumentParser

parser = ArgumentParser()
parser.add_argument(
    "-c",
    "--config",
    dest="config_filename",
    help="read config from FILE",
    metavar="CONFIG_FILE",
)
parser.add_argument(
    "-n",
    "--namespace",
    dest="namespace_to_parse",
    help="namespace to parse resources",
    metavar="NAMESPACE_TO_PARCE",
)
parser.add_argument(
    "-t",
    "--time-to-parse",
    dest="time_to_parse",
    help="days range to parse resources (default 1d)",
    metavar="NAMESPACE_TO_PARCE",
)
parser.add_argument(
    "-o",
    "--output-file",
    dest="output_file_path",
    help="path to output file (if not defined file will be saved in local dir of running script {./resources.yaml})",
    metavar="OUTPUT_FILE_PATH",
)
args = parser.parse_args()

if not args.time_to_parse:
    time_to_parse = "1d"
else:
    time_to_parse = args.time_to_parse

if args.config_filename:
    config_file = args.config_filename
else:
    config_file = "calc_config.yaml"

if args.output_file_path:
    output_file_path = args.output_file_path


prom_request_url = "http://127.0.0.1:8080/api/v1/query"
default_label_args = 'container!="",image!=""'
percentile_value = 90


def read_file(file_path: str):
    with open(file_path, "r") as stream:
        try:
            data_from_file = yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            print(exc)

    return data_from_file


config = read_file(config_file)


def write_file(data, name):
    try:
        with open(name, "w") as open_file:
            open_file.write(data)
    except OSError as error:
        print("File '%s' can not be created" % name)


def get_response(query):
    try:
        r = requests.get(prom_request_url, params=query)
    except requests.exceptions.HTTPError as e:
        print(e.response.text, file=stderr)

    data = r.json()
    value = data

    return value


def get_cpu_query(namespace, label_args):
    query_str = 'sum by (pod)(quantile_over_time(0.95,rate(container_cpu_usage_seconds_total{{namespace="{}",{}}}[1m])[{}:1m])) * 1000'.format(
        namespace, label_args, time_to_parse
    )
    query = {"query": query_str}
    print(query)

    return query


def get_mem_query(namespace, label_args):
    query_str = 'max(max_over_time(container_memory_max_usage_bytes{{namespace="{}",{}}} [{}:5m])) by (pod) /(1024* 1024)'.format(
        namespace, label_args, time_to_parse
    )
    query = {"query": query_str}
    print(query)

    return query


def get_label_args(labels_args):
    if labels_args:
        result = "{},{}".format(default_label_args, labels_args)
    else:
        result = default_label_args

    return result


def make_default_resources(value):
    if value == 0 or value < 10:
        value = 10

    return value


def slice_pod_name(pod):
    name = pod[:-5]
    if name[-1] == "-":
        new_name = name[:-1]
    else:
        new_name = name

    return new_name


def extract_resources_data(cpu_data, mem_data):
    resources = {}

    if len(cpu_data["data"]["result"]) != 0:
        for item in cpu_data["data"]["result"]:
            pod = slice_pod_name(item["metric"]["pod"])
            metric = item["value"][1]
            metric = int(round((float(item["value"][1])), 0))

            if pod not in resources.keys():
                resources[pod] = {}

            if "cpu" not in resources[pod].keys():
                resources[pod]["cpu"] = []

            resources[pod]["cpu"].append(metric)

    if len(mem_data["data"]["result"]) != 0:
        for item in mem_data["data"]["result"]:
            pod = slice_pod_name(item["metric"]["pod"])
            # metric = item["value"][1]
            metric = int(round((float(item["value"][1])), 0))

            if pod not in resources.keys():
                resources[pod] = {}

            if "memory" not in resources[pod].keys():
                resources[pod]["memory"] = []

            resources[pod]["memory"].append(metric)

    return resources


def get_resources(namespace, label_args):
    resources = {}

    labels = get_label_args(label_args)
    query_mem = get_mem_query(namespace, labels)
    query_cpu = get_cpu_query(namespace, labels)
    get_cpu_stat = get_response(query_cpu)
    get_mem_stat = get_response(query_mem)

    return extract_resources_data(get_cpu_stat, get_mem_stat)


def calculate_resources(data):

    resources = {}

    for app in data:
        if app not in resources.keys():
            resources[app] = {}

        for item in ["cpu", "memory"]:
            if item not in data[app]:
                print("{} has no key cpu. adding null value".format(app))
                data[app][item] = []
                data[app][item].append(0)

        sorted_list_cpu = sorted(data[app]["cpu"])
        cpu_percentile_index = round(
            ((percentile_value / 100) * len(data[app]["cpu"])) - 1
        )
        cpu_percentile = sorted_list_cpu[cpu_percentile_index]
        resources[app]["cpu"] = "{}m".format(make_default_resources(cpu_percentile))

        sorted_list_mem = sorted(data[app]["memory"])
        mem_percentile_index = round(
            ((percentile_value / 100) * len(data[app]["memory"])) - 1
        )
        mem_percentile = sorted_list_mem[mem_percentile_index]
        resources[app]["memory"] = "{}Mi".format(make_default_resources(mem_percentile))

    return resources


def create_resources_config(data):
    file = "resources.j2"
    templateLoader = jinja2.FileSystemLoader(searchpath="./")
    templateEnv = jinja2.Environment(loader=templateLoader)
    TEMPLATE_FILE = file
    template = templateEnv.get_template(TEMPLATE_FILE)
    outputText = template.render(data=data)

    return outputText


def exec(namespace):

    if namespace in config.keys():
        if "label_args" in config[namespace].keys():
            labels_args = config[namespace]["label_args"]
        else:
            labels_args = ""
    else:
        labels_args = ""

    raw_data = get_resources(namespace, labels_args)
    print(raw_data)

    if namespace in config.keys():
        if "remove_pods" in config[namespace]:
            for remove_pod in config[namespace]["remove_pods"]:
                raw_data.pop(remove_pod, None)

    pod_resources = calculate_resources(raw_data)
    resources_file = create_resources_config(pod_resources)


    if not args.output_file_path:
        path = "resources/{}".format(namespace)

        try:
            os.makedirs(path, exist_ok=True)
        except OSError as error:
            print("Directory '%s' can not be created" % path)

        output_file_path = "{}/resources.yaml".format(path)
    else:
        output_file_path = args.output_file_path

    write_file(resources_file, output_file_path)


if __name__ == "__main__":

    if args.namespace_to_parse:
        exec(args.namespace_to_parse)
    else:
        for namespace in config:
            exec(namespace)
