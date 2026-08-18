# GCP → Terraform Agent v2

Converts a live GCP project (or an exported CSV) into production-ready Terraform.

## What's new in v2

| Feature | v1 | v2 |
|---|---|---|
| LLM backend | Gemini only | **Claude + Gemini** (auto-detected) |
| Inventory source | CSV/Excel upload required | **`--project PROJECT_ID`** (live API) or CSV |
| Terraform output | monolithic `main.tf` | **Split files** per resource type |

---

## Quick start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Set your LLM API key

Claude (recommended):
```bash
export ANTHROPIC_API_KEY='sk-ant-...'
```

Or Gemini:
```bash
export GEMINI_API_KEY='AIza...'
```

Both keys set? The agent auto-picks Claude. Override with:
```bash
export LLM_PROVIDER=claude   # or gemini
```

### 3. Run — live GCP project (recommended)

```bash
# Authenticate first
gcloud auth application-default login
gcloud services enable cloudasset.googleapis.com --project=my-project-id

# Generate Terraform
python -m agent.main --project my-project-id
```

Limit inventory by resource type. The agent asks Gemini to generate Cloud Asset
Inventory SQL for the supplied value. If Gemini is not configured, common
resource names fall back to deterministic SQL templates:

```bash
python -m agent.main --project my-project-id --resource-type subnet
python -m agent.main --project my-project-id --resource-type firewall --resource-type address
python -m agent.main --project my-project-id --resource-type vpc --resource-type dns
python -m agent.main --project my-project-id --resource-type router-nat --resource-type interconnect-attachment
```

Resource type values can be specific resources or resource families.

Specific resource examples fetch only that asset type:

```bash
python -m agent.main --project my-project-id --resource-type network
python -m agent.main --project my-project-id --resource-type subnet
python -m agent.main --project my-project-id --resource-type firewall
python -m agent.main --project my-project-id --resource-type router
```

Family examples expand into multiple related asset types:

| Resource type value | Fetches |
|---|---|
| `vpc` or `networking` | `ComputeNetwork`, `ComputeSubnetwork`, `ComputeAddress`, `ComputeGlobalAddress`, `ComputeRouter`, `ComputeInterconnectAttachment`, `ServiceNetworkingConnection` |
| `address`, `addresses`, `ip`, or `ips` | `ComputeAddress`, `ComputeGlobalAddress` |
| `dns` | `DNSManagedZone`, `DNSRecordSet` |
| `router-nat`, `router-interface`, `router-peer` | `ComputeRouter`; NATs, interfaces, and peers are extracted from router additional attributes |
| `shared-vpc-host-project`, `shared-vpc-service-project`, `iam-partial-policy` | Project-level inventory used for Shared VPC and IAM tfvars sections |

For address/IP inventory, the agent writes these fields when Cloud Asset
`Additional attributes` contains values:

```hcl
addresses = {
  my_ip = {
    project      = "my-project"
    region       = "us-central1"
    scope        = "regional"
    address      = "10.0.0.5"
    description  = "reserved internal address"
    address_type = "INTERNAL"
    network_tier = "PREMIUM"
  }
}
```

Optional fields are not defaulted. If `address`, `description`,
`addressType`, or `networkTier` is missing or blank in `Additional attributes`,
that attribute is skipped in tfvars.

You can still run only for subnet:

```bash
python -m agent.main --project my-project-id --resource-type subnet
```

That fetches only `ComputeSubnetwork` rows. For complete Terraform planning,
make sure the matching VPC network rows are already in
`inventory_cache/<project>_inventory.csv`, or fetch them in the same project
cache first:

```bash
python -m agent.main --project my-project-id --resource-type network
python -m agent.main --project my-project-id --resource-type subnet
```

or fetch the VPC family in one run:

```bash
python -m agent.main --project my-project-id --resource-type vpc
```

Live project runs merge into `inventory_cache/<project>_inventory.csv` by
default. For example, run `--resource-type network` first and
`--resource-type subnet` later; the second run keeps the network inventory and
adds or updates subnet rows before regenerating Terraform.

To replace the cached inventory instead:

```bash
python -m agent.main --project my-project-id --resource-type subnet --reset-inventory
```

You can also provide the Cloud Asset SQL directly:

```bash
python -m agent.main --project my-project-id --resource-type network --asset-query 'SELECT resource.data.name AS Name, "compute.Network" AS Resource_type, REGEXP_EXTRACT(name, r"projects/([^/]+)") AS Project_Id, "global" AS Location, TO_JSON_STRING(resource.data) AS Additional_attributes FROM compute_googleapis_com_Network ORDER BY resource.data.name'
```

### 3b. Run — legacy CSV mode

```bash
python -m agent.main --inventory gcp-asset-inventory.csv
```

### Resource group files

The agent also writes paired files under `terraform_output/resource_groups/`:

- `resource_groups/firewall/terraform.tfvars` contains all `ComputeFirewall` rules.
- `resource_groups/firewall/imports.tf` contains the matching firewall import blocks.
- `resource_groups/dns/terraform.tfvars` contains `DNSManagedZone` and `DNSRecordSet` rows.
- `resource_groups/dns/imports.tf` contains the matching DNS import blocks.
- `resource_groups/vpc-<network>/terraform.tfvars` contains one VPC and its associated resources.
- `resource_groups/vpc-<network>/imports.tf` contains the matching import blocks for that VPC group.

VPC group files include these sections when inventory is available:

```text
ComputeNetwork
ComputeSubnetwork
ComputeAddress
ComputeGlobalAddress
ComputeRouterNAT
ServiceNetworkingConnection
ComputeSharedVPCHostProject
ComputeRouter
ComputeInterconnectAttachment
ComputeRouterInterface
ComputeRouterPeer
ComputeSharedVPCServiceProject
IAMPartialPolicy
```

---

## Output structure

```
terraform_output/
├── providers.tf      ← provider & terraform version block
├── backend.tf        ← GCS remote state configuration
├── variables.tf      ← all variable declarations
├── outputs.tf        ← all output declarations
├── terraform.tfvars  ← generated values from your inventory
├── resource_groups/  ← paired tfvars/imports by resource group
│   ├── vpc-<network>/
│   │   ├── terraform.tfvars
│   │   └── imports.tf
│   ├── firewall/
│   │   ├── terraform.tfvars
│   │   └── imports.tf
│   └── dns/
│       ├── terraform.tfvars
│       └── imports.tf
├── imports.tf        ← Terraform import blocks for existing resources
│
├── vpcs.tf           ← module "vpc" call
├── subnets.tf        ← module "subnet" call
├── firewalls.tf      ← module "firewall" call
├── routers.tf        ← module "router" call
├── addresses.tf      ← module "address" call
├── negs.tf           ← module "neg" (Network Endpoint Groups) call
│
└── modules/
    ├── vpc/           main.tf · variables.tf · outputs.tf
    ├── subnet/        main.tf · variables.tf · outputs.tf
    ├── firewall/      main.tf · variables.tf · outputs.tf
    ├── router/        main.tf · variables.tf · outputs.tf
    ├── address/       main.tf · variables.tf · outputs.tf
    └── neg/           main.tf · variables.tf · outputs.tf
```

---

`terraform_output/terraform.tfvars` is the file Terraform reads by default
when you run `terraform plan` from `terraform_output`. It now includes the
same supported resource families preserved in `resource_groups/*/terraform.tfvars`.
The `resource_groups/` files remain useful for reviewing or importing one VPC,
firewall, or DNS group at a time.

`extras.tf` wires the additional root modules for Router NAT, Service
Networking Connection, Interconnect Attachment, Router Interface, Router Peer,
Shared VPC host/service projects, DNS, and project IAM members derived from
IAM partial-policy bindings.

### Planning modes

Run an overall plan from the Terraform output directory:

```bash
cd terraform_output
terraform plan
```

Terraform automatically reads `terraform_output/terraform.tfvars`, so this
plans all wired resources from the inventory.

Run a resource-group-specific plan by passing the group's tfvars file:

```bash
cd terraform_output
terraform plan -var-file="resource_groups/vpc-<network>/terraform.tfvars"
terraform plan -var-file="resource_groups/firewall/terraform.tfvars"
terraform plan -var-file="resource_groups/dns/terraform.tfvars"
```

The resource group files live under `terraform_output/resource_groups/`.
They are useful for reviewing or planning one VPC, firewall, or DNS group at a
time. The root Terraform modules are still loaded, so group tfvars files keep
unrelated maps empty or omitted where possible.

## Deploy

```bash
cd terraform_output

# Create GCS bucket for state (once only)
gsutil mb -p my-project-id gs://my-project-id-tfstate

terraform init
terraform validate
terraform plan
terraform apply
```

---

## Requirements

- Python ≥ 3.11
- Terraform ≥ 1.6
- Google Cloud SDK (`gcloud`) — only needed for `--project` mode
- Permission to query Cloud Asset resources on the target project
  (`cloudasset.assets.queryAssets`)
- Permission to use the project as the API consumer
  (`serviceusage.services.use`)

## Troubleshooting

### `USER_PROJECT_DENIED` or `serviceusage.services.use`

If `gcloud asset query` fails with `USER_PROJECT_DENIED`, the active gcloud
account can authenticate, but it is not allowed to use the target project for
the Cloud Asset API request.

Ask a project IAM admin to grant both permissions:

```bash
gcloud projects add-iam-policy-binding my-project-id \
  --member="user:YOUR_EMAIL" \
  --role="roles/serviceusage.serviceUsageConsumer"

gcloud projects add-iam-policy-binding my-project-id \
  --member="user:YOUR_EMAIL" \
  --role="roles/cloudasset.viewer"
```

Then retry:

```bash
python -m agent.main --project my-project-id --resource-type vpc --resource-type dns
```
