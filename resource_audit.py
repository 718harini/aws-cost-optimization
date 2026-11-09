import boto3
import json
import os

from datetime import datetime, timezone, timedelta
from botocore.exceptions import ClientError, BotoCoreError


# ==========================================
# 1. AWS CONFIGURATION
# ==========================================

REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")

CPU_THRESHOLD = 10
MIN_CPU_DATAPOINTS = 3
CPU_ANALYSIS_DAYS = 7
CPU_METRIC_PERIOD = 86400

session = boto3.Session(region_name=REGION)

ec2 = session.client("ec2")
cloudwatch = session.client("cloudwatch")


# ==========================================
# 2. DATA COLLECTION
# ==========================================

instances = []
volumes = []
addresses = []
cpu_metrics = []
findings = []


# ==========================================
# 3. EC2 RESOURCE AUDIT
# ==========================================

def audit_ec2():

    print("\nStarting EC2 audit...")

    paginator = ec2.get_paginator("describe_instances")

    for page in paginator.paginate():

        for reservation in page["Reservations"]:

            for instance in reservation["Instances"]:

                state = instance["State"]["Name"]
                instance_id = instance["InstanceId"]

                instances.append({
                    "InstanceId": instance_id,
                    "InstanceType": instance["InstanceType"],
                    "State": state
                })

                if state == "stopped":

                    findings.append({
                        "Resource": instance_id,
                        "Type": "EC2",
                        "Issue": "Stopped instance",
                        "Recommendation": (
                            "Review whether this instance is still required."
                        )
                    })

    print(f"EC2 instances discovered: {len(instances)}")


# ==========================================
# 4. EBS VOLUME AUDIT
# ==========================================

def audit_ebs():

    print("\nStarting EBS audit...")

    paginator = ec2.get_paginator("describe_volumes")

    for page in paginator.paginate():

        for volume in page["Volumes"]:

            state = volume["State"]

            volumes.append({
                "VolumeId": volume["VolumeId"],
                "SizeGB": volume["Size"],
                "State": state
            })

            if state == "available":

                findings.append({
                    "Resource": volume["VolumeId"],
                    "Type": "EBS",
                    "Issue": "Unattached volume",
                    "Recommendation": (
                        "Verify its data and ownership before considering deletion."
                    )
                })

    print(f"EBS volumes discovered: {len(volumes)}")


# ==========================================
# 5. ELASTIC IP AUDIT
# ==========================================

def audit_elastic_ips():

    print("\nStarting Elastic IP audit...")

    paginator = ec2.get_paginator("describe_addresses")

    for page in paginator.paginate():

        for address in page["Addresses"]:

            associated = "AssociationId" in address

            addresses.append({
                "PublicIp": address["PublicIp"],
                "AllocationId": address["AllocationId"],
                "Associated": associated
            })

            if not associated:

                findings.append({
                    "Resource": address["AllocationId"],
                    "Type": "Elastic IP",
                    "Issue": "Unassociated Elastic IP",
                    "Recommendation": (
                        "Verify whether this IP is required before releasing it."
                    )
                })

    print(f"Elastic IP addresses discovered: {len(addresses)}")


# ==========================================
# 6. CLOUDWATCH CPU AUDIT
# ==========================================

def audit_cpu():

    print("\nStarting CloudWatch CPU analysis...")

    end_time = datetime.now(timezone.utc)

    start_time = end_time - timedelta(days=CPU_ANALYSIS_DAYS)

    for instance in instances:

        if instance["State"] != "running":
            continue

        instance_id = instance["InstanceId"]

        try:

            response = cloudwatch.get_metric_statistics(

                Namespace="AWS/EC2",

                MetricName="CPUUtilization",

                Dimensions=[
                    {
                        "Name": "InstanceId",
                        "Value": instance_id
                    }
                ],

                StartTime=start_time,

                EndTime=end_time,

                Period=CPU_METRIC_PERIOD,

                Statistics=[
                    "Average",
                    "Maximum",
                    "SampleCount"
                ]
            )

            datapoints = response["Datapoints"]

            datapoints.sort(
                key=lambda point: point["Timestamp"]
            )

            average_cpu = None
            maximum_cpu = None

            if datapoints:

                total_samples = sum(
                    point["SampleCount"]
                    for point in datapoints
                )

                if total_samples > 0:

                    average_cpu = sum(
                        point["Average"] * point["SampleCount"]
                        for point in datapoints
                    ) / total_samples

                maximum_cpu = max(
                    point["Maximum"]
                    for point in datapoints
                )

            metric_result = {

                "InstanceId": instance_id,

                "AverageCPUUtilization": (
                    round(average_cpu, 2)
                    if average_cpu is not None else None
                ),

                "MaximumCPUUtilization": (
                    round(maximum_cpu, 2)
                    if maximum_cpu is not None else None
                ),

                "DatapointsAvailable": len(datapoints),

                "AnalysisPeriodDays": CPU_ANALYSIS_DAYS,

                "MetricPeriodSeconds": CPU_METRIC_PERIOD
            }

            cpu_metrics.append(metric_result)

            if len(datapoints) < MIN_CPU_DATAPOINTS:

                print(
                    f"Insufficient CPU history for {instance_id}: "
                    f"{len(datapoints)} datapoint(s)"
                )

                continue

            if average_cpu is not None and average_cpu < CPU_THRESHOLD:

                findings.append({

                    "Resource": instance_id,

                    "Type": "EC2",

                    "Issue": "Potentially underutilized instance",

                    "AverageCPU": round(average_cpu, 2),

                    "Recommendation": (
                        "Review CPU, memory, network and application "
                        "requirements before considering right-sizing."
                    )
                })

        except ClientError as error:

            error_message = error.response["Error"]["Message"]

            print(
                f"CloudWatch error for {instance_id}: {error_message}"
            )

            cpu_metrics.append({

                "InstanceId": instance_id,

                "Error": error_message

            })

    print(f"CPU metrics collected: {len(cpu_metrics)}")


# ==========================================
# 7. GENERATE SUMMARY
# ==========================================

def generate_summary():

    summary = {

        "TotalEC2Instances": len(instances),

        "RunningInstances": sum(
            1 for instance in instances
            if instance["State"] == "running"
        ),

        "StoppedInstances": sum(
            1 for instance in instances
            if instance["State"] == "stopped"
        ),

        "TotalEBSVolumes": len(volumes),

        "UnattachedVolumes": sum(
            1 for volume in volumes
            if volume["State"] == "available"
        ),

        "TotalElasticIPs": len(addresses),

        "UnassociatedElasticIPs": sum(
            1 for address in addresses
            if not address["Associated"]
        ),

        "CPUInstancesAnalyzed": sum(
            1 for metric in cpu_metrics
            if "Error" not in metric
        ),

        "PotentialOptimizationFindings": len(findings)

    }

    return summary


# ==========================================
# 8. GENERATE JSON REPORT
# ==========================================

def generate_report():

    summary = generate_summary()

    report = {

        "AuditTime": datetime.now(timezone.utc).isoformat(),

        "Region": REGION,

        "Configuration": {

            "CPUThreshold": CPU_THRESHOLD,

            "MinimumDatapoints": MIN_CPU_DATAPOINTS,

            "AnalysisPeriodDays": CPU_ANALYSIS_DAYS,

            "MetricPeriodSeconds": CPU_METRIC_PERIOD

        },

        "Summary": summary,

        "EC2Instances": instances,

        "EBSVolumes": volumes,

        "ElasticIPs": addresses,

        "CloudWatchCPUMetrics": cpu_metrics,

        "OptimizationFindings": findings

    }

    with open("audit_report.json", "w", encoding="utf-8") as file:

        json.dump(report, file, indent=4)

    return summary


# ==========================================
# 9. DISPLAY RESULTS
# ==========================================

def display_results(summary):

    print("\n========== AWS COST OPTIMIZATION AUDIT ==========")

    print(json.dumps(summary, indent=4))

    print("\n========== CLOUDWATCH CPU METRICS ==========")

    print(json.dumps(cpu_metrics, indent=4))

    print("\n========== OPTIMIZATION FINDINGS ==========")

    if findings:

        for finding in findings:

            print(f"\nResource: {finding['Resource']}")

            print(f"Type: {finding['Type']}")

            print(f"Issue: {finding['Issue']}")

            print(f"Recommendation: {finding['Recommendation']}")

    else:

        print("No potential optimization findings detected.")

    print("\nAudit report saved as audit_report.json")


# ==========================================
# 10. MAIN EXECUTION
# ==========================================

def main():

    try:

        print("\nStarting AWS Resource Audit...")

        print(f"Region: {REGION}")

        audit_ec2()

        audit_ebs()

        audit_elastic_ips()

        audit_cpu()

        summary = generate_report()

        display_results(summary)

    except (ClientError, BotoCoreError) as error:

        print("\nAWS API ERROR:")

        print(error)

    except OSError as error:

        print("\nFILE ERROR:")

        print(error)


if __name__ == "__main__":

    main()
