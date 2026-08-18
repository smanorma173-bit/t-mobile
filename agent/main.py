"""
main.py — Entry point for GCP → Terraform Agent v2.

Usage (live GCP inventory — recommended):
    python -m agent.main --project my-gcp-project-id

Usage (legacy CSV mode):
    python -m agent.main --inventory gcp-asset-inventory.csv

LLM backend (for tfvars generation):
    export ANTHROPIC_API_KEY='sk-ant-...'   # Claude (preferred)
    export GEMINI_API_KEY='AIza...'          # Gemini (fallback)
    export LLM_PROVIDER=claude|gemini|auto   # force a backend (default: auto)

GCP authentication (for --project mode):
    gcloud auth application-default login
    gcloud services enable cloudasset.googleapis.com --project=PROJECT_ID
"""

import argparse
import sys
from agent.config import OUTPUT_DIR
from agent.inventory import load
from agent.generator import run


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate Terraform from a GCP project or asset inventory CSV."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--project", metavar="PROJECT_ID",
        help="GCP project ID — fetches live inventory via Cloud Asset API (gcloud)."
    )
    source.add_argument(
        "--inventory", metavar="CSV_PATH",
        help="Path to a previously exported gcp-asset-inventory.csv (legacy mode)."
    )
    parser.add_argument(
        "--resource-type",
        action="append",
        default=[],
        metavar="TYPE",
        help=(
            "Resource type or resource family to inventory. Can be repeated "
            "or comma-separated. Gemini generates the Cloud Asset access query "
            "from this value."
        ),
    )
    parser.add_argument(
        "--access-query",
        "--asset-query",
        dest="access_query",
        metavar="QUERY",
        help=(
            "Optional full Cloud Asset queryAssets SQL override. When omitted "
            "and --resource-type is provided, Gemini generates this SQL."
        ),
    )
    parser.add_argument(
        "--reset-inventory",
        action="store_true",
        help=(
            "Replace the cached project inventory CSV instead of merging the "
            "newly fetched resources into it."
        ),
    )
    args = parser.parse_args()

    print("\n🤖  GCP → Terraform Agent v2 starting...\n")

    if args.project:
        from agent.inventory.gcp_fetcher import fetch
        rows = fetch(
            args.project,
            resource_types=args.resource_type,
            access_query=args.access_query,
            merge_existing=not args.reset_inventory,
        )
        inventory = load(rows)
    else:
        inventory = load(args.inventory)

    print("\n📦  Inventory loaded:")
    print(f"    Project  : {inventory['project_id']}")
    print(f"    VPCs     : {len(inventory['vpcs'])}")
    print(f"    Subnets  : {len(inventory['subnets'])}")
    print(f"    Firewalls: {len(inventory['firewalls'])}")
    print(f"    Routers  : {len(inventory['routers'])}")
    print(f"    Addresses: {len(inventory['addresses'])}")
    print(f"    Router NATs       : {len(inventory.get('router_nats', []))}")
    print(f"    Router interfaces : {len(inventory.get('router_interfaces', []))}")
    print(f"    Router peers      : {len(inventory.get('router_peers', []))}")
    print(f"    Interconnect attach: {len(inventory.get('interconnect_attachments', []))}")
    print(f"    Service networking: {len(inventory.get('service_networking_connections', []))}")
    print(f"    DNS zones         : {len(inventory.get('dns_managed_zones', []))}")
    print(f"    DNS record sets   : {len(inventory.get('dns_record_sets', []))}")
    print(f"    NEGs     : {len(inventory['negs'])}")
    print()

    run(inventory)

    print(f"\n✅  Done — files written to: {OUTPUT_DIR}\n")
    print("Terraform output structure:")
    print("  terraform_output/")
    print("  ├── providers.tf      ← provider & terraform block")
    print("  ├── backend.tf        ← GCS remote state")
    print("  ├── variables.tf      ← all variable declarations")
    print("  ├── outputs.tf        ← all output declarations")
    print("  ├── terraform.tfvars  ← generated values")
    print("  ├── resource_groups/  ← paired tfvars/imports by resource group")
    print("  ├── imports.tf        ← Terraform import blocks")
    print("  ├── vpcs.tf           ← module \"vpc\" call")
    print("  ├── subnets.tf        ← module \"subnet\" call")
    print("  ├── firewalls.tf      ← module \"firewall\" call")
    print("  ├── routers.tf        ← module \"router\" call")
    print("  ├── addresses.tf      ← module \"address\" call")
    print("  ├── negs.tf           ← module \"neg\" call")
    print("  ├── extras.tf         ← additional root module calls")
    print("  └── modules/")
    print("      ├── vpc/")
    print("      ├── subnet/")
    print("      ├── firewall/")
    print("      ├── router/")
    print("      ├── address/")
    print("      └── neg/")
    print()
    print("Next steps (Cloud Shell / local):")
    pid = inventory["project_id"]
    print(f"  cd terraform_output")
    print(f"  gsutil mb -p {pid} gs://{pid}-tfstate")
    print(f"  terraform init")
    print(f"  terraform validate")
    print(f"  terraform plan")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        sys.exit(1)
