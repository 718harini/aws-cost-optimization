import boto3
import json
from datetime import datetime, timezone, timedelta

# ==========================================
# AWS CONFIGURATION
# ==========================================

REGION = "us-east-1"
CPU_THRESHOLD = 10
MIN_CPU_DATAPOINTS = 3

ec2 = boto3.client("ec2", region_name=REGION)
cloudwatch = boto3.client("cloudwatch", region_name=REGION)

instances = []
volumes = []
addresses = []
cpu_metrics = []
findings = []


# ==========================================
# 1. EC2 RESOURCE AUDIT
# ==========================================

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
                    "Recommendation": "Review whether this instance is still required."
                })


# ==========================================
# 2. EBS VOLUME AUDIT
# ==========================================

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
                "Recommendation": "Verify its data and ownership before considering deletion."
            })


# ==========================================
# 3. ELASTIC IP AUDIT
# ==========================================

response = ec2.describe_addresses()

for address in response["Addresses"]:

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
            "Recommendation": "Verify whether this IP is required before releasing it."
        })


# ==========================================
# 4. CLOUDWATCH CPU AUDIT
# ==========================================

end_time = datetime.now(timezone.utc)
start_time = end_time - timedelta(days=7)

for instance in instances:

    if instance["State"] != "running":
        continue

    instance_id = instance["InstanceId"]

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
        Period=86400,
        Statistics=["Average", "Maximum"]
    )

    datapoints = response["Datapoints"]

    datapoints.sort(key=lambda x: x["Timestamp"])

    if datapoints:

        average_cpu = sum(
            point["Average"] for point in datapoints
        ) / len(datapoints)

        maximum_cpu = max(
            point["Maximum"] for point in datapoints
        )

    else:
        average_cpu = None
        maximum_cpu = None

    cpu_metrics.append({
        "InstanceId": instance_id,
        "AverageCPUUtilization": round(average_cpu, 2)
            if average_cpu is not None else None,
        "MaximumCPUUtilization": round(maximum_cpu, 2)
            if maximum_cpu is not None else None,
        "DatapointsAvailable": len(datapoints)
    })

    if len(datapoints) < MIN_CPU_DATAPOINTS:

        print(
            f"Insufficient CPU history for {instance_id}: "
            f"{len(datapoints)} datapoint(s)"
        )

    elif average_cpu < CPU_THRESHOLD:

        findings.append({
            "Resource": instance_id,
            "Type": "EC2",
            "Issue": "Potentially underutilized instance",
            "AverageCPU": round(average_cpu, 2),
            "Recommendation": (
                "Review instance utilization and consider right-sizing "
                "if memory, network and application requirements also permit."
            )
        })


# ==========================================
# 5. GENERATE SUMMARY
# ==========================================

summary = {

    "TotalEC2Instances": len(instances),

    "RunningInstances": sum(
        1 for i in instances if i["State"] == "running"
    ),

    "StoppedInstances": sum(
        1 for i in instances if i["State"] == "stopped"
    ),

    "TotalEBSVolumes": len(volumes),

    "UnattachedVolumes": sum(
        1 for v in volumes if v["State"] == "available"
    ),

    "TotalElasticIPs": len(addresses),

    "UnassociatedElasticIPs": sum(
        1 for a in addresses if not a["Associated"]
    ),

    "CPUInstancesAnalyzed": len(cpu_metrics),

    "PotentialOptimizationFindings": len(findings)
}


# ==========================================
# 6. CREATE AUDIT REPORT
# ==========================================

report = {

    "AuditTime": datetime.now(timezone.utc).isoformat(),

    "Region": REGION,

    "Summary": summary,

    "EC2Instances": instances,

    "EBSVolumes": volumes,

    "ElasticIPs": addresses,

    "CloudWatchCPUMetrics": cpu_metrics,

    "OptimizationFindings": findings
}


# ==========================================
# 7. SAVE JSON REPORT
# ==========================================

with open("audit_report.json", "w") as file:

    json.dump(report, file, indent=4)


# ==========================================
# 8. DISPLAY RESULTS
# ==========================================

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
