#!/usr/bin/env python3
"""Builds a small stack (network, subnet, security group, instance), then
tears it down. This is the SDK's end-to-end example.

    export NEMAU_API_URL=https://api.nemau-cloud.com
    export NEMAU_ACCESS_KEY_ID=...  NEMAU_ACCESS_KEY_SECRET=...
    python examples/provision.py --image ubuntu-24.04
"""

from __future__ import annotations

import argparse
import sys
import time

from nemau import TARGET_INSTANCE, NemauClient, NemauTaskFailedError  # noqa: F401


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="id of the image to boot")
    parser.add_argument("--name", default="sdk-example", help="prefix for the resources created")
    parser.add_argument("--keep", action="store_true", help="do not tear down at the end")
    args = parser.parse_args()

    client = NemauClient()
    print(f"api {client.base_url}, credential {client.describe_credential()}")

    # One deadline for the whole run rather than a timeout per step.
    deadline = time.monotonic() + 20 * 60

    network = client.network.create_network_and_wait(
        f"{args.name}-net", "created by the SDK example"
    )
    print(f"network {network.id} ({network.name})")

    subnet_ref = client.network.create_subnet(
        network.id, name=f"{args.name}-subnet", cidr="10.42.0.0/24"
    )
    client.wait_for(subnet_ref, deadline=deadline)
    print("subnet 10.42.0.0/24 ready")

    sg_ref = client.network.create_security_group(f"{args.name}-sg", "ssh only")
    sg_id, _ = client.wait_for_resource_id(sg_ref, "security_group_id", deadline=deadline)
    rule_ref = client.network.create_security_group_rule(
        sg_id,
        direction="ingress",
        protocol="tcp",
        port_range_min=22,
        port_range_max=22,
        remote_ip_prefix="0.0.0.0/0",
    )
    client.wait_for(rule_ref, deadline=deadline)
    print(f"security group {sg_id} allows tcp/22")

    instance = client.compute.create_instance_and_wait(
        name=args.name,
        image_id=args.image,
        vcpus=2,
        ram_mb=4096,
        disk_gb=40,
        network_id=network.id,
        security_groups=[sg_id],
    )
    print(f"instance {instance.id} is {instance.status}, addresses {instance.addresses}")

    if args.keep:
        print("--keep: nothing is torn down")
        return 0

    # Delete in reverse dependency order: a network cannot be deleted while an
    # instance still has a port on it.
    print("tearing down")
    for ref in (
        client.compute.delete_instance(instance.id),
        client.network.delete_security_group(sg_id),
        client.network.delete_network(network.id),
    ):
        client.wait_for(ref, deadline=deadline)
    print("done")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except NemauTaskFailedError as error:
        # The message includes the task id, which support needs.
        print(f"operation failed: {error}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
