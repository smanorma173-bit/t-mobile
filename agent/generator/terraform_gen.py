"""
generator/terraform_gen.py
Writes all Terraform files deterministically.

Key improvement over v1:
  The root terraform_output/ no longer has a monolithic main.tf.
  Instead resources are split into focused files:
    - vpcs.tf        : module "vpc" call
    - subnets.tf     : module "subnet" call
    - firewalls.tf   : module "firewall" call
    - routers.tf     : module "router" call
    - addresses.tf   : module "address" call
    - negs.tf        : module "neg" / Network Endpoint Groups call

  Shared config lives where it always did:
    - providers.tf, backend.tf, variables.tf, outputs.tf, terraform.tfvars
"""

import json
import shutil
from pathlib import Path
from agent.config import OUTPUT_DIR
from agent.utils import write
from agent.generator.tfvars_writer import generate as gen_tfvars
from agent.generator.tfvars_writer import generate_split as gen_split_tfvars

# ── Root: providers.tf ────────────────────────────────────────────────────

_PROVIDERS_TF = """\
terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
"""

# ── Root: backend.tf ──────────────────────────────────────────────────────

_BACKEND_TF = """\
# backend.tf — GCS remote state.
# Create the bucket once before `terraform init`:
#   gsutil mb -p {project_id} gs://{project_id}-tfstate
terraform {{
  backend "gcs" {{
    bucket = "{project_id}-tfstate"
    prefix = "network/terraform.tfstate"
  }}
}}
"""

# ── Root: split module-call files ─────────────────────────────────────────

_VPCS_TF = """\
# vpcs.tf — VPC network resources.
# No dependencies on other modules.

module "vpc" {
  source = "./modules/vpc"
  vpcs   = var.vpcs
}
"""

_SUBNETS_TF = """\
# subnets.tf — Subnet resources.
# Depends on: module.vpc (for network self_links)

module "subnet" {
  source         = "./modules/subnet"
  subnets        = var.subnets
  vpc_self_links = module.vpc.network_self_link
}
"""

_FIREWALLS_TF = """\
# firewalls.tf — Firewall rules.
# Depends on: module.vpc (for network self_links)

module "firewall" {
  source         = "./modules/firewall"
  firewalls      = var.firewalls
  vpc_self_links = module.vpc.network_self_link
}
"""

_ROUTERS_TF = """\
# routers.tf — Cloud Routers (and implicitly Cloud NAT).
# Depends on: module.vpc (for network self_links)

module "router" {
  source         = "./modules/router"
  routers        = var.routers
  vpc_self_links = module.vpc.network_self_link
}
"""

_ADDRESSES_TF = """\
# addresses.tf — Static IP addresses (global and regional).
# No dependencies on other modules.

module "address" {
  source    = "./modules/address"
  addresses = var.addresses
}
"""

_NEGS_TF = """\
# negs.tf — Network Endpoint Groups.
# Depends on: module.vpc (for network self_links)

module "neg" {
  source         = "./modules/neg"
  negs           = var.negs
  vpc_self_links = module.vpc.network_self_link
}
"""

# ── Root: variables.tf ────────────────────────────────────────────────────

_EXTRAS_TF = """\
# extras.tf - Additional VPC, DNS, and Shared VPC resources.

module "router_nat" {
  source      = "./modules/router_nat"
  router_nats = var.router_nats
}

module "service_networking_connection" {
  source                         = "./modules/service_networking_connection"
  service_networking_connections = var.service_networking_connections
  vpc_self_links                 = module.vpc.network_self_link
}

module "interconnect_attachment" {
  source                   = "./modules/interconnect_attachment"
  interconnect_attachments = var.interconnect_attachments
}

module "router_interface" {
  source            = "./modules/router_interface"
  router_interfaces = var.router_interfaces
}

module "router_peer" {
  source       = "./modules/router_peer"
  router_peers = var.router_peers
}

module "shared_vpc_host_project" {
  source                   = "./modules/shared_vpc_host_project"
  shared_vpc_host_projects = var.shared_vpc_host_projects
}

module "shared_vpc_service_project" {
  source                      = "./modules/shared_vpc_service_project"
  shared_vpc_service_projects = var.shared_vpc_service_projects
}

module "dns" {
  source            = "./modules/dns"
  dns_managed_zones = var.dns_managed_zones
  dns_record_sets   = var.dns_record_sets
}

module "iam_partial_policy" {
  source               = "./modules/iam_partial_policy"
  iam_partial_policies = var.iam_partial_policies
}
"""

_VARIABLES_TF = """\
variable "project_id" {
  description = "GCP project ID."
  type        = string
}

variable "region" {
  description = "Default GCP region."
  type        = string
  default     = "us-central1"
}

variable "vpcs" {
  description = "Map of VPC networks. Key = network name."
  type = map(object({
    project                 = string
    auto_create_subnetworks = optional(bool, null)
    description             = optional(string, null)
    routing_mode            = optional(string, null)
  }))
  default = {}
}

variable "subnets" {
  description = "Map of subnets. Key = subnet name."
  type = map(object({
    project                  = string
    region                   = string
    ip_cidr_range            = string
    parent_vpc               = string
    private_ip_google_access = optional(bool, null)
    description              = optional(string, null)
    secondary_ip_ranges = optional(list(object({
      range_name    = string
      ip_cidr_range = string
    })), [])
    stack_type = optional(string, null)
    log_config = optional(object({
      aggregation_interval = optional(string, null)
      flow_sampling        = optional(number, null)
      metadata             = optional(string, null)
      metadata_fields      = optional(list(string), null)
      filter_expr          = optional(string, null)
    }), null)
  }))
  default = {}
}

variable "firewalls" {
  description = "Map of firewall rules. Key = rule name."
  type = map(object({
    project       = string
    parent_vpc    = string
    direction     = optional(string, "INGRESS")
    priority      = optional(number, null)
    description   = optional(string, null)
    disabled      = optional(bool, null)
    source_ranges = optional(list(string), null)
    destination_ranges = optional(list(string), null)
    source_tags   = optional(list(string), null)
    source_service_accounts = optional(list(string), null)
    target_tags   = optional(list(string), null)
    target_service_accounts = optional(list(string), null)
    log_config = optional(object({
      metadata = optional(string, "INCLUDE_ALL_METADATA")
    }), null)
    allow = optional(list(object({
      protocol = string
      ports    = optional(list(string), [])
    })), [])
    deny = optional(list(object({
      protocol = string
      ports    = optional(list(string), [])
    })), [])
  }))
  default = {}
}

variable "routers" {
  description = "Map of Cloud Routers. Key = router name."
  type = map(object({
    project    = string
    region     = string
    parent_vpc = string
  }))
  default = {}
}

variable "addresses" {
  description = "Map of IP addresses (global and regional). Key = address name."
  type = map(object({
    project = string
    region  = string
    scope   = string
    address = optional(string, null)
    description  = optional(string, null)
    address_type = optional(string, null)
    network_tier = optional(string, null)
  }))
  default = {}
}

variable "negs" {
  description = "Map of user-managed NEGs. Auto-managed GKE NEGs (k8s1-*, k8s2-*, gke-*) are excluded."
  type = map(object({
    project               = string
    zone                  = string
    parent_vpc            = string
    network_endpoint_type = optional(string, "GCE_VM_IP_PORT")
  }))
  default = {}
}

variable "router_nats" {
  description = "Router NAT details extracted from compute.Router inventory."
  type        = any
  default     = {}
}

variable "service_networking_connections" {
  description = "Service Networking connection details."
  type        = any
  default     = {}
}

variable "shared_vpc_host_projects" {
  description = "Shared VPC host project details."
  type        = any
  default     = {}
}

variable "interconnect_attachments" {
  description = "Cloud Interconnect attachment details."
  type        = any
  default     = {}
}

variable "router_interfaces" {
  description = "Cloud Router interface details extracted from compute.Router inventory."
  type        = any
  default     = {}
}

variable "router_peers" {
  description = "Cloud Router BGP peer details extracted from compute.Router inventory."
  type        = any
  default     = {}
}

variable "shared_vpc_service_projects" {
  description = "Shared VPC service project details."
  type        = any
  default     = {}
}

variable "iam_partial_policies" {
  description = "IAM partial policy details."
  type        = any
  default     = {}
}

variable "dns_managed_zones" {
  description = "Cloud DNS managed zone details."
  type        = any
  default     = {}
}

variable "dns_record_sets" {
  description = "Cloud DNS record set details."
  type        = any
  default     = {}
}
"""

# ── Root: outputs.tf ──────────────────────────────────────────────────────

_OUTPUTS_TF = """\
output "vpc_self_links" {
  description = "VPC self_links keyed by name."
  value       = module.vpc.network_self_link
}

output "subnet_self_links" {
  description = "Subnet self_links keyed by name."
  value       = module.subnet.subnet_self_link
}

output "firewall_self_links" {
  description = "Firewall self_links keyed by name."
  value       = module.firewall.firewall_self_link
}

output "router_self_links" {
  description = "Router self_links keyed by name."
  value       = module.router.router_self_link
}

output "global_address_self_links" {
  description = "Global address self_links keyed by name."
  value       = module.address.global_address_self_link
}

output "regional_address_self_links" {
  description = "Regional address self_links keyed by name."
  value       = module.address.regional_address_self_link
}

output "neg_self_links" {
  description = "NEG self_links keyed by name."
  value       = module.neg.neg_self_link
}
"""

# ── Module: vpc ───────────────────────────────────────────────────────────

_VPC_MAIN = """\
resource "google_compute_network" "this" {
  for_each                = var.vpcs
  name                    = each.key
  project                 = each.value.project
  auto_create_subnetworks = each.value.auto_create_subnetworks
  description             = each.value.description
  routing_mode            = each.value.routing_mode
}
"""

_VPC_VARS = """\
variable "vpcs" {
  description = "Map of VPC networks. Key = network name."
  type = map(object({
    project                 = string
    auto_create_subnetworks = optional(bool, null)
    description             = optional(string, null)
    routing_mode            = optional(string, null)
  }))
}
"""

_VPC_OUTPUTS = """\
output "network_self_link" {
  value = { for k, v in google_compute_network.this : k => v.self_link }
}
output "network_name" {
  value = { for k, v in google_compute_network.this : k => v.name }
}
"""

# ── Module: subnet ────────────────────────────────────────────────────────

_SUBNET_MAIN = """\
resource "google_compute_subnetwork" "this" {
  for_each                 = var.subnets
  name                     = each.key
  project                  = each.value.project
  region                   = each.value.region
  ip_cidr_range            = each.value.ip_cidr_range
  network                  = var.vpc_self_links[each.value.parent_vpc]
  private_ip_google_access = each.value.private_ip_google_access
  description              = each.value.description
  stack_type               = each.value.stack_type != null && contains(["IPV4_ONLY", "IPV4_IPV6", "IPV6_ONLY"], each.value.stack_type) ? each.value.stack_type : null

  dynamic "secondary_ip_range" {
    for_each = each.value.secondary_ip_ranges
    content {
      range_name    = secondary_ip_range.value.range_name
      ip_cidr_range = secondary_ip_range.value.ip_cidr_range
    }
  }

  dynamic "log_config" {
    for_each = each.value.log_config == null ? [] : [each.value.log_config]
    content {
      aggregation_interval = log_config.value.aggregation_interval
      flow_sampling        = log_config.value.flow_sampling
      metadata             = log_config.value.metadata
      metadata_fields      = log_config.value.metadata_fields
      filter_expr          = log_config.value.filter_expr
    }
  }
}
"""

_SUBNET_VARS = """\
variable "subnets" {
  type = map(object({
    project                  = string
    region                   = string
    ip_cidr_range            = string
    parent_vpc               = string
    private_ip_google_access = optional(bool, null)
    description              = optional(string, null)
    secondary_ip_ranges = optional(list(object({
      range_name    = string
      ip_cidr_range = string
    })), [])
    stack_type = optional(string, null)
    log_config = optional(object({
      aggregation_interval = optional(string, null)
      flow_sampling        = optional(number, null)
      metadata             = optional(string, null)
      metadata_fields      = optional(list(string), null)
      filter_expr          = optional(string, null)
    }), null)
  }))
}
variable "vpc_self_links" {
  description = "From module.vpc.network_self_link"
  type        = map(string)
}
"""

_SUBNET_OUTPUTS = """\
output "subnet_self_link" {
  value = { for k, v in google_compute_subnetwork.this : k => v.self_link }
}
output "subnet_name" {
  value = { for k, v in google_compute_subnetwork.this : k => v.name }
}
"""

# ── Module: firewall ──────────────────────────────────────────────────────

_FIREWALL_MAIN = """\
locals {
  # Firewalls with no allow/deny rules: inject a placeholder allow-all so
  # Terraform does not reject the resource. Review and tighten after import.
  firewalls_normalised = {
    for k, v in var.firewalls : k => merge(v, {
      allow = (length(v.allow) == 0 && length(v.deny) == 0) ? [
        { protocol = "all", ports = [] }
      ] : v.allow
      deny = (length(v.allow) == 0 && length(v.deny) == 0) ? [] : v.deny
    })
  }
}

resource "google_compute_firewall" "this" {
  for_each    = local.firewalls_normalised
  name        = each.key
  project     = each.value.project
  network     = var.vpc_self_links[each.value.parent_vpc]
  direction   = each.value.direction
  priority    = each.value.priority
  description = each.value.description
  disabled    = each.value.disabled

  source_ranges = each.value.direction == "INGRESS" ? (
    each.value.source_ranges != null ? each.value.source_ranges : (
      each.value.source_tags == null && each.value.source_service_accounts == null ? ["0.0.0.0/0"] : null
    )
  ) : null

  destination_ranges       = each.value.direction == "EGRESS" ? each.value.destination_ranges : null
  source_tags              = each.value.direction == "INGRESS" ? each.value.source_tags : null
  source_service_accounts  = each.value.direction == "INGRESS" ? each.value.source_service_accounts : null
  target_tags              = each.value.target_tags
  target_service_accounts  = each.value.target_service_accounts

  dynamic "log_config" {
    for_each = each.value.log_config == null ? [] : [each.value.log_config]
    content {
      metadata = log_config.value.metadata
    }
  }

  dynamic "allow" {
    for_each = each.value.allow
    content {
      protocol = allow.value.protocol
      ports    = allow.value.ports
    }
  }

  dynamic "deny" {
    for_each = each.value.deny
    content {
      protocol = deny.value.protocol
      ports    = deny.value.ports
    }
  }

  lifecycle {
    # Rule details (ports, protocols, ranges) are managed in tfvars.
    # Prevent accidental drift if imported from existing rules.
    ignore_changes = [description]
  }
}
"""

_FIREWALL_VARS = """\
variable "firewalls" {
  type = map(object({
    project       = string
    parent_vpc    = string
    direction     = optional(string, "INGRESS")
    priority      = optional(number, null)
    description   = optional(string, null)
    disabled      = optional(bool, null)
    source_ranges = optional(list(string), null)
    destination_ranges = optional(list(string), null)
    source_tags   = optional(list(string), null)
    source_service_accounts = optional(list(string), null)
    target_tags   = optional(list(string), null)
    target_service_accounts = optional(list(string), null)
    log_config = optional(object({
      metadata = optional(string, "INCLUDE_ALL_METADATA")
    }), null)
    allow = optional(list(object({
      protocol = string
      ports    = optional(list(string), [])
    })), [])
    deny = optional(list(object({
      protocol = string
      ports    = optional(list(string), [])
    })), [])
  }))
}
variable "vpc_self_links" {
  description = "From module.vpc.network_self_link"
  type        = map(string)
}
"""

_FIREWALL_OUTPUTS = """\
output "firewall_self_link" {
  value = { for k, v in google_compute_firewall.this : k => v.self_link }
}
"""

# ── Module: router ────────────────────────────────────────────────────────

_ROUTER_MAIN = """\
resource "google_compute_router" "this" {
  for_each = var.routers
  name     = each.key
  project  = each.value.project
  region   = each.value.region
  network  = var.vpc_self_links[each.value.parent_vpc]
}
"""

_ROUTER_VARS = """\
variable "routers" {
  type = map(object({
    project    = string
    region     = string
    parent_vpc = string
  }))
}
variable "vpc_self_links" {
  description = "From module.vpc.network_self_link"
  type        = map(string)
}
"""

_ROUTER_OUTPUTS = """\
output "router_self_link" {
  value = { for k, v in google_compute_router.this : k => v.self_link }
}
"""

# ── Module: address ───────────────────────────────────────────────────────

_ADDRESS_MAIN = """\
resource "google_compute_global_address" "this" {
  for_each     = { for k, v in var.addresses : k => v if v.scope == "global" }
  name         = each.key
  project      = each.value.project
  address      = each.value.address != null && each.value.address != "" ? each.value.address : null
  description  = each.value.description
  address_type = each.value.address_type
}

resource "google_compute_address" "this" {
  for_each     = { for k, v in var.addresses : k => v if v.scope == "regional" }
  name         = each.key
  project      = each.value.project
  region       = each.value.region
  address      = each.value.address != null && each.value.address != "" ? each.value.address : null
  description  = each.value.description
  address_type = each.value.address_type
  network_tier = each.value.network_tier
}
"""

_ADDRESS_VARS = """\
variable "addresses" {
  type = map(object({
    project = string
    region  = string
    scope   = string
    address = optional(string, null)
    description  = optional(string, null)
    address_type = optional(string, null)
    network_tier = optional(string, null)
  }))
}
"""

_ADDRESS_OUTPUTS = """\
output "global_address_self_link" {
  value = { for k, v in google_compute_global_address.this : k => v.self_link }
}
output "regional_address_self_link" {
  value = { for k, v in google_compute_address.this : k => v.self_link }
}
"""

# ── Module: neg ───────────────────────────────────────────────────────────

_NEG_MAIN = """\
resource "google_compute_network_endpoint_group" "this" {
  for_each              = var.negs
  name                  = each.key
  project               = each.value.project
  zone                  = each.value.zone
  network               = var.vpc_self_links[each.value.parent_vpc]
  network_endpoint_type = each.value.network_endpoint_type
}
"""

_NEG_VARS = """\
variable "negs" {
  type = map(object({
    project               = string
    zone                  = string
    parent_vpc            = string
    network_endpoint_type = optional(string, "GCE_VM_IP_PORT")
  }))
}
variable "vpc_self_links" {
  description = "From module.vpc.network_self_link"
  type        = map(string)
}
"""

_NEG_OUTPUTS = """\
output "neg_self_link" {
  value = { for k, v in google_compute_network_endpoint_group.this : k => v.self_link }
}
"""

_ROUTER_NAT_MAIN = """\
locals {
  router_nats = {
    for k, v in var.router_nats : k => v
    if try(v.router, null) != null
      && try(v.region, null) != null
      && lookup(try(v.attributes, {}), "natIpAllocateOption", null) != null
      && lookup(try(v.attributes, {}), "sourceSubnetworkIpRangesToNat", null) != null
  }
}

resource "google_compute_router_nat" "this" {
  for_each = local.router_nats

  name                               = try(each.value.source_name, each.key)
  project                            = each.value.project
  region                             = each.value.region
  router                             = each.value.router
  nat_ip_allocate_option             = lookup(try(each.value.attributes, {}), "natIpAllocateOption", null)
  source_subnetwork_ip_ranges_to_nat = lookup(try(each.value.attributes, {}), "sourceSubnetworkIpRangesToNat", null)
  min_ports_per_vm                   = lookup(try(each.value.attributes, {}), "minPortsPerVm", null)
  enable_endpoint_independent_mapping = lookup(try(each.value.attributes, {}), "enableEndpointIndependentMapping", null)

  dynamic "log_config" {
    for_each = lookup(try(each.value.attributes, {}), "logConfig", null) == null ? [] : [lookup(each.value.attributes, "logConfig", {})]
    content {
      enable = lookup(log_config.value, "enable", null)
      filter = lookup(log_config.value, "filter", null)
    }
  }
}
"""

_ROUTER_NAT_VARS = """\
variable "router_nats" {
  type    = any
  default = {}
}
"""

_GENERIC_OUTPUTS = """\
output "ids" {
  value = { for k, v in try(values(local.resources), []) : k => v }
}
"""

_SERVICE_NETWORKING_MAIN = """\
locals {
  connections = {
    for k, v in var.service_networking_connections : k => v
    if try(v.parent_vpc, null) != null
      && length(lookup(try(v.attributes, {}), "reservedPeeringRanges", [])) > 0
  }
}

resource "google_service_networking_connection" "this" {
  for_each = local.connections

  network                 = lookup(var.vpc_self_links, each.value.parent_vpc, each.value.parent_vpc)
  service                 = lookup(try(each.value.attributes, {}), "service", "servicenetworking.googleapis.com")
  reserved_peering_ranges = lookup(try(each.value.attributes, {}), "reservedPeeringRanges", [])
}
"""

_SERVICE_NETWORKING_VARS = """\
variable "service_networking_connections" {
  type    = any
  default = {}
}

variable "vpc_self_links" {
  type    = map(string)
  default = {}
}
"""

_INTERCONNECT_ATTACHMENT_MAIN = """\
locals {
  interconnect_attachments = {
    for k, v in var.interconnect_attachments : k => v
    if try(v.location, null) != null
      && lookup(try(v.attributes, {}), "router", null) != null
      && lookup(try(v.attributes, {}), "type", null) != null
  }
}

resource "google_compute_interconnect_attachment" "this" {
  for_each = local.interconnect_attachments

  name                     = try(each.value.source_name, each.key)
  project                  = each.value.project
  region                   = each.value.location
  router                   = lookup(each.value.attributes, "router", null)
  type                     = lookup(each.value.attributes, "type", null)
  description              = lookup(each.value.attributes, "description", null)
  admin_enabled            = lookup(each.value.attributes, "adminEnabled", null)
  edge_availability_domain = lookup(each.value.attributes, "edgeAvailabilityDomain", null)
  vlan_tag8021q            = lookup(each.value.attributes, "vlanTag8021q", null)
  mtu                      = lookup(each.value.attributes, "mtu", null)
}
"""

_INTERCONNECT_ATTACHMENT_VARS = """\
variable "interconnect_attachments" {
  type    = any
  default = {}
}
"""

_ROUTER_INTERFACE_MAIN = """\
locals {
  router_interfaces = {
    for k, v in var.router_interfaces : k => v
    if try(v.router, null) != null
      && try(v.region, null) != null
      && lookup(try(v.attributes, {}), "ipRange", null) != null
  }
}

resource "google_compute_router_interface" "this" {
  for_each = local.router_interfaces

  name                    = try(each.value.source_name, each.key)
  project                 = each.value.project
  region                  = each.value.region
  router                  = each.value.router
  ip_range                = lookup(each.value.attributes, "ipRange", null)
  vpn_tunnel              = lookup(each.value.attributes, "linkedVpnTunnel", null)
  interconnect_attachment = lookup(each.value.attributes, "linkedInterconnectAttachment", null)
  subnetwork              = lookup(each.value.attributes, "subnetwork", null)
}
"""

_ROUTER_INTERFACE_VARS = """\
variable "router_interfaces" {
  type    = any
  default = {}
}
"""

_ROUTER_PEER_MAIN = """\
locals {
  router_peers = {
    for k, v in var.router_peers : k => v
    if try(v.router, null) != null
      && try(v.region, null) != null
      && lookup(try(v.attributes, {}), "peerIpAddress", null) != null
      && lookup(try(v.attributes, {}), "peerAsn", null) != null
      && lookup(try(v.attributes, {}), "interfaceName", null) != null
  }
}

resource "google_compute_router_peer" "this" {
  for_each = local.router_peers

  name                      = try(each.value.source_name, each.key)
  project                   = each.value.project
  region                    = each.value.region
  router                    = each.value.router
  interface                 = lookup(each.value.attributes, "interfaceName", null)
  peer_ip_address           = lookup(each.value.attributes, "peerIpAddress", null)
  peer_asn                  = lookup(each.value.attributes, "peerAsn", null)
  advertised_route_priority = lookup(each.value.attributes, "advertisedRoutePriority", null)
}
"""

_ROUTER_PEER_VARS = """\
variable "router_peers" {
  type    = any
  default = {}
}
"""

_SHARED_VPC_HOST_MAIN = """\
locals {
  shared_vpc_host_projects = {
    for k, v in var.shared_vpc_host_projects : k => v
    if try(v.project, null) != null || try(v.source_name, null) != null
  }
}

resource "google_compute_shared_vpc_host_project" "this" {
  for_each = local.shared_vpc_host_projects
  project  = try(each.value.source_name, each.value.project)
}
"""

_SHARED_VPC_HOST_VARS = """\
variable "shared_vpc_host_projects" {
  type    = any
  default = {}
}
"""

_SHARED_VPC_SERVICE_MAIN = """\
locals {
  shared_vpc_service_projects = {
    for k, v in var.shared_vpc_service_projects : k => v
    if (try(v.project, null) != null || try(v.source_name, null) != null)
      && (lookup(try(v.attributes, {}), "hostProject", null) != null || lookup(try(v.attributes, {}), "host_project", null) != null)
  }
}

resource "google_compute_shared_vpc_service_project" "this" {
  for_each        = local.shared_vpc_service_projects
  host_project    = coalesce(lookup(each.value.attributes, "hostProject", null), lookup(each.value.attributes, "host_project", null))
  service_project = try(each.value.source_name, each.value.project)
}
"""

_SHARED_VPC_SERVICE_VARS = """\
variable "shared_vpc_service_projects" {
  type    = any
  default = {}
}
"""

_DNS_MAIN = """\
locals {
  managed_zones = {
    for k, v in var.dns_managed_zones : k => v
    if try(v.dns_name, null) != null
  }

  record_sets = {
    for k, v in var.dns_record_sets : k => v
    if try(v.managed_zone, null) != null
      && try(v.record_type, null) != null
      && try(v.ttl, null) != null
      && length(try(v.rrdatas, [])) > 0
  }
}

resource "google_dns_managed_zone" "this" {
  for_each = local.managed_zones

  name        = try(each.value.source_name, each.key)
  project     = each.value.project
  dns_name    = each.value.dns_name
  #description = lookup(try(each.value.attributes, {}), "description", null)
  description = (
    trimspace(lookup(try(each.value.attributes, {}), "description", "")) != ""
    ? lookup(try(each.value.attributes, {}), "description", "")
    : null
  )
  visibility  = lower(lookup(try(each.value.attributes, {}), "visibility", null))
}

resource "google_dns_record_set" "this" {
  for_each = local.record_sets

  project      = each.value.project
  managed_zone = each.value.managed_zone
  name         = coalesce(lookup(try(each.value.attributes, {}), "name", null), try(each.value.dns_name, null), try(each.value.source_name, each.key))
  type         = each.value.record_type
  ttl          = try(each.value.ttl, null)
  rrdatas      = each.value.rrdatas
}
"""

_DNS_VARS = """\
variable "dns_managed_zones" {
  type    = any
  default = {}
}

variable "dns_record_sets" {
  type    = any
  default = {}
}
"""

_IAM_PARTIAL_POLICY_MAIN = """\
locals {
  iam_member_maps = [
    for policy_key, policy in var.iam_partial_policies : {
      for item in flatten([
        for binding in lookup(try(policy.attributes, {}), "bindings", []) : [
          for member in lookup(binding, "members", []) : {
            key     = "${policy_key}-${sha1(join("|", [lookup(binding, "role", ""), member]))}"
            project = try(policy.project, null)
            role    = lookup(binding, "role", null)
            member  = member
          }
        ]
      ]) : item.key => item
      if item.project != null && item.role != null && item.member != null
    }
  ]

  iam_members = length(local.iam_member_maps) == 0 ? {} : merge(local.iam_member_maps...)
}

resource "google_project_iam_member" "this" {
  for_each = local.iam_members

  project = each.value.project
  role    = each.value.role
  member  = each.value.member
}
"""

_IAM_PARTIAL_POLICY_VARS = """\
variable "iam_partial_policies" {
  type    = any
  default = {}
}
"""


# ── Orchestrator ──────────────────────────────────────────────────────────

def run(inventory: dict) -> None:
    o   = OUTPUT_DIR
    pid = inventory["project_id"]
    _clean_generated_group_dirs(o)

    print("\n  📝  Writing root configuration files ...")
    _w(o / "providers.tf",     _PROVIDERS_TF)
    _w(o / "backend.tf",       _BACKEND_TF.format(project_id=pid))
    _w(o / "variables.tf",     _VARIABLES_TF)
    _w(o / "outputs.tf",       _OUTPUTS_TF)
    _w(o / "terraform.tfvars", gen_tfvars(inventory))
    for rel_path, content in gen_split_tfvars(inventory).items():
        _w(o / rel_path, content)
    for rel_path, content in gen_split_imports(inventory).items():
        _w(o / rel_path, content)
    _w(o / "imports.tf",       gen_imports(inventory))

    print("\n  📝  Writing resource-specific module-call files ...")
    _w(o / "vpcs.tf",      _VPCS_TF)
    _w(o / "subnets.tf",   _SUBNETS_TF)
    _w(o / "firewalls.tf", _FIREWALLS_TF)
    _w(o / "routers.tf",   _ROUTERS_TF)
    _w(o / "addresses.tf", _ADDRESSES_TF)
    _w(o / "negs.tf",      _NEGS_TF)
    _w(o / "extras.tf",    _EXTRAS_TF)

    print("\n  📝  Writing module implementations ...")
    _w(o / "modules/vpc/main.tf",         _VPC_MAIN)
    _w(o / "modules/vpc/variables.tf",    _VPC_VARS)
    _w(o / "modules/vpc/outputs.tf",      _VPC_OUTPUTS)

    _w(o / "modules/subnet/main.tf",      _SUBNET_MAIN)
    _w(o / "modules/subnet/variables.tf", _SUBNET_VARS)
    _w(o / "modules/subnet/outputs.tf",   _SUBNET_OUTPUTS)

    _w(o / "modules/firewall/main.tf",      _FIREWALL_MAIN)
    _w(o / "modules/firewall/variables.tf", _FIREWALL_VARS)
    _w(o / "modules/firewall/outputs.tf",   _FIREWALL_OUTPUTS)

    _w(o / "modules/router/main.tf",      _ROUTER_MAIN)
    _w(o / "modules/router/variables.tf", _ROUTER_VARS)
    _w(o / "modules/router/outputs.tf",   _ROUTER_OUTPUTS)

    _w(o / "modules/address/main.tf",      _ADDRESS_MAIN)
    _w(o / "modules/address/variables.tf", _ADDRESS_VARS)
    _w(o / "modules/address/outputs.tf",   _ADDRESS_OUTPUTS)

    _w(o / "modules/neg/main.tf",      _NEG_MAIN)
    _w(o / "modules/neg/variables.tf", _NEG_VARS)
    _w(o / "modules/neg/outputs.tf",   _NEG_OUTPUTS)

    _w(o / "modules/router_nat/main.tf",      _ROUTER_NAT_MAIN)
    _w(o / "modules/router_nat/variables.tf", _ROUTER_NAT_VARS)
    _w(o / "modules/router_nat/outputs.tf",   "")

    _w(o / "modules/service_networking_connection/main.tf",      _SERVICE_NETWORKING_MAIN)
    _w(o / "modules/service_networking_connection/variables.tf", _SERVICE_NETWORKING_VARS)
    _w(o / "modules/service_networking_connection/outputs.tf",   "")

    _w(o / "modules/interconnect_attachment/main.tf",      _INTERCONNECT_ATTACHMENT_MAIN)
    _w(o / "modules/interconnect_attachment/variables.tf", _INTERCONNECT_ATTACHMENT_VARS)
    _w(o / "modules/interconnect_attachment/outputs.tf",   "")

    _w(o / "modules/router_interface/main.tf",      _ROUTER_INTERFACE_MAIN)
    _w(o / "modules/router_interface/variables.tf", _ROUTER_INTERFACE_VARS)
    _w(o / "modules/router_interface/outputs.tf",   "")

    _w(o / "modules/router_peer/main.tf",      _ROUTER_PEER_MAIN)
    _w(o / "modules/router_peer/variables.tf", _ROUTER_PEER_VARS)
    _w(o / "modules/router_peer/outputs.tf",   "")

    _w(o / "modules/shared_vpc_host_project/main.tf",      _SHARED_VPC_HOST_MAIN)
    _w(o / "modules/shared_vpc_host_project/variables.tf", _SHARED_VPC_HOST_VARS)
    _w(o / "modules/shared_vpc_host_project/outputs.tf",   "")

    _w(o / "modules/shared_vpc_service_project/main.tf",      _SHARED_VPC_SERVICE_MAIN)
    _w(o / "modules/shared_vpc_service_project/variables.tf", _SHARED_VPC_SERVICE_VARS)
    _w(o / "modules/shared_vpc_service_project/outputs.tf",   "")

    _w(o / "modules/dns/main.tf",      _DNS_MAIN)
    _w(o / "modules/dns/variables.tf", _DNS_VARS)
    _w(o / "modules/dns/outputs.tf",   "")

    _w(o / "modules/iam_partial_policy/main.tf",      _IAM_PARTIAL_POLICY_MAIN)
    _w(o / "modules/iam_partial_policy/variables.tf", _IAM_PARTIAL_POLICY_VARS)
    _w(o / "modules/iam_partial_policy/outputs.tf",   "")


def gen_imports(inventory: dict) -> str:
    lines = [
        "# imports.tf - Terraform import blocks for discovered GCP resources.",
        "# Review terraform.tfvars before running terraform plan/apply.",
        "",
    ]
    project_id = inventory["project_id"]

    for item in inventory["vpcs"]:
        _add_import(
            lines,
            f"module.vpc.google_compute_network.this[{_hcl_string(item['name'])}]",
            f"projects/{_project(item, project_id)}/global/networks/{_source_name(item)}",
        )

    for item in inventory["subnets"]:
        _add_import(
            lines,
            f"module.subnet.google_compute_subnetwork.this[{_hcl_string(item['name'])}]",
            f"projects/{_project(item, project_id)}/regions/{item['region']}/subnetworks/{_source_name(item)}",
        )

    for item in inventory["firewalls"]:
        _add_import(
            lines,
            f"module.firewall.google_compute_firewall.this[{_hcl_string(item['name'])}]",
            f"projects/{_project(item, project_id)}/global/firewalls/{_source_name(item)}",
        )

    for item in inventory["routers"]:
        _add_import(
            lines,
            f"module.router.google_compute_router.this[{_hcl_string(item['name'])}]",
            f"projects/{_project(item, project_id)}/regions/{item['region']}/routers/{_source_name(item)}",
        )

    for item in inventory["addresses"]:
        if item.get("scope") == "global":
            to_address = "module.address.google_compute_global_address.this"
            import_id = f"projects/{_project(item, project_id)}/global/addresses/{_source_name(item)}"
        else:
            to_address = "module.address.google_compute_address.this"
            import_id = (
                f"projects/{_project(item, project_id)}/regions/{item['region']}"
                f"/addresses/{_source_name(item)}"
            )
        _add_import(lines, f"{to_address}[{_hcl_string(item['name'])}]", import_id)

    for item in inventory["negs"]:
        _add_import(
            lines,
            f"module.neg.google_compute_network_endpoint_group.this[{_hcl_string(item['name'])}]",
            f"projects/{_project(item, project_id)}/zones/{item['zone']}/networkEndpointGroups/{_source_name(item)}",
        )

    for item in inventory.get("interconnect_attachments", []):
        _add_import(
            lines,
            f"module.interconnect_attachment.google_compute_interconnect_attachment.this[{_hcl_string(item['name'])}]",
            f"projects/{_project(item, project_id)}/regions/{item['location']}/interconnectAttachments/{_source_name(item)}",
        )

    for item in inventory.get("router_nats", []):
        _add_import(
            lines,
            f"module.router_nat.google_compute_router_nat.this[{_hcl_string(item['name'])}]",
            _router_child_import_id(item, project_id),
        )

    for item in inventory.get("router_interfaces", []):
        _add_import(
            lines,
            f"module.router_interface.google_compute_router_interface.this[{_hcl_string(item['name'])}]",
            _router_child_import_id(item, project_id),
        )

    for item in inventory.get("router_peers", []):
        _add_import(
            lines,
            f"module.router_peer.google_compute_router_peer.this[{_hcl_string(item['name'])}]",
            _router_child_import_id(item, project_id),
        )

    for item in inventory.get("service_networking_connections", []):
        network = _network_import_name(item)
        service = (item.get("attributes") or {}).get("service") or "servicenetworking.googleapis.com"
        _add_import(
            lines,
            f"module.service_networking_connection.google_service_networking_connection.this[{_hcl_string(item['name'])}]",
            f"projects/{_project(item, project_id)}/global/networks/{network}:{service}",
        )

    for item in inventory.get("shared_vpc_host_projects", []):
        _add_import(
            lines,
            f"module.shared_vpc_host_project.google_compute_shared_vpc_host_project.this[{_hcl_string(item['name'])}]",
            _project(item, project_id),
        )

    for item in inventory.get("shared_vpc_service_projects", []):
        _add_import(
            lines,
            f"module.shared_vpc_service_project.google_compute_shared_vpc_service_project.this[{_hcl_string(item['name'])}]",
            _source_name(item),
        )

    for item in inventory.get("dns_managed_zones", []):
        _add_import(
            lines,
            f"module.dns.google_dns_managed_zone.this[{_hcl_string(item['name'])}]",
            f"projects/{_project(item, project_id)}/managedZones/{_source_name(item)}",
        )

    for item in inventory.get("dns_record_sets", []):
        zone = item.get("managed_zone")
        record_name = (item.get("attributes") or {}).get("name") or item.get("dns_name") or _source_name(item)
        record_type = item.get("record_type")
        if zone and record_type:
            _add_import(
                lines,
                f"module.dns.google_dns_record_set.this[{_hcl_string(item['name'])}]",
                (
                    f"projects/{_project(item, project_id)}/managedZones/{zone}"
                    f"/rrsets/{record_name}/{record_type}"
                ),
            )

    if inventory.get("iam_partial_policies"):
        lines.extend([
            "# IAM partial policy rows are preserved in terraform.tfvars.",
            "# Add project/folder/org IAM resource blocks before importing IAM policy state.",
            "",
        ])

    if len(lines) == 3:
        lines.append("# No supported resources were found to import.")
    return "\n".join(lines).rstrip() + "\n"


def gen_split_imports(inventory: dict) -> dict[str, str]:
    files: dict[str, str] = {}
    project_id = inventory["project_id"]
    known_vpcs = {v["name"] for v in inventory.get("vpcs", [])}

    for vpc in inventory.get("vpcs", []):
        files[f"resource_groups/vpc-{vpc['name']}/imports.tf"] = _vpc_imports(
            inventory, vpc["name"], project_id
        )

    if _has_unassigned_vpc_import_items(inventory, known_vpcs):
        files["resource_groups/vpc-unassigned/imports.tf"] = _vpc_imports(
            inventory, None, project_id, known_vpcs
        )

    files["resource_groups/firewall/imports.tf"] = _firewall_imports(inventory, project_id)
    files["resource_groups/dns/imports.tf"] = _dns_imports(inventory, project_id)
    return files


def _vpc_imports(
    inventory: dict,
    vpc_name: str | None,
    project_id: str,
    known_vpcs: set[str] | None = None,
) -> str:
    title = f"VPC group: {vpc_name or 'unassigned'}"
    lines = _import_header(title)
    routers = _by_vpc(inventory.get("routers", []), vpc_name, known_vpcs)
    router_names = {item["name"] for item in routers}

    for item in inventory.get("vpcs", []):
        if vpc_name and item["name"] == vpc_name:
            _add_import(
                lines,
                f"module.vpc.google_compute_network.this[{_hcl_string(item['name'])}]",
                f"projects/{_project(item, project_id)}/global/networks/{_source_name(item)}",
            )

    for item in _by_vpc(inventory.get("subnets", []), vpc_name, known_vpcs):
        _add_import(
            lines,
            f"module.subnet.google_compute_subnetwork.this[{_hcl_string(item['name'])}]",
            f"projects/{_project(item, project_id)}/regions/{item['region']}/subnetworks/{_source_name(item)}",
        )

    for item in _by_vpc(inventory.get("addresses", []), vpc_name, known_vpcs):
        _add_address_import(lines, item, project_id)

    for item in routers:
        _add_import(
            lines,
            f"module.router.google_compute_router.this[{_hcl_string(item['name'])}]",
            f"projects/{_project(item, project_id)}/regions/{item['region']}/routers/{_source_name(item)}",
        )

    for item in _by_vpc(inventory.get("interconnect_attachments", []), vpc_name, known_vpcs):
        _add_import(
            lines,
            f"module.interconnect_attachment.google_compute_interconnect_attachment.this[{_hcl_string(item['name'])}]",
            f"projects/{_project(item, project_id)}/regions/{item['location']}/interconnectAttachments/{_source_name(item)}",
        )

    for item in _by_router(inventory.get("router_nats", []), router_names):
        _add_import(
            lines,
            f"module.router_nat.google_compute_router_nat.this[{_hcl_string(item['name'])}]",
            _router_child_import_id(item, project_id),
        )

    for item in _by_router(inventory.get("router_interfaces", []), router_names):
        _add_import(
            lines,
            f"module.router_interface.google_compute_router_interface.this[{_hcl_string(item['name'])}]",
            _router_child_import_id(item, project_id),
        )

    for item in _by_router(inventory.get("router_peers", []), router_names):
        _add_import(
            lines,
            f"module.router_peer.google_compute_router_peer.this[{_hcl_string(item['name'])}]",
            _router_child_import_id(item, project_id),
        )

    for item in _by_vpc(inventory.get("service_networking_connections", []), vpc_name, known_vpcs):
        network = _network_import_name(item)
        service = (item.get("attributes") or {}).get("service") or "servicenetworking.googleapis.com"
        _add_import(
            lines,
            f"module.service_networking_connection.google_service_networking_connection.this[{_hcl_string(item['name'])}]",
            f"projects/{_project(item, project_id)}/global/networks/{network}:{service}",
        )

    if vpc_name is None:
        for item in inventory.get("shared_vpc_host_projects", []):
            _add_import(
                lines,
                f"module.shared_vpc_host_project.google_compute_shared_vpc_host_project.this[{_hcl_string(item['name'])}]",
                _project(item, project_id),
            )
        for item in inventory.get("shared_vpc_service_projects", []):
            _add_import(
                lines,
                f"module.shared_vpc_service_project.google_compute_shared_vpc_service_project.this[{_hcl_string(item['name'])}]",
                _source_name(item),
            )
        if inventory.get("iam_partial_policies"):
            lines.extend([
                "# IAM partial policy rows are preserved in terraform.tfvars.",
                "# Add project/folder/org IAM resource blocks before importing IAM policy state.",
                "",
            ])

    return _finish_imports(lines)


def _firewall_imports(inventory: dict, project_id: str) -> str:
    lines = _import_header("Firewall rules")
    for item in inventory.get("firewalls", []):
        _add_import(
            lines,
            f"module.firewall.google_compute_firewall.this[{_hcl_string(item['name'])}]",
            f"projects/{_project(item, project_id)}/global/firewalls/{_source_name(item)}",
        )
    return _finish_imports(lines)


def _dns_imports(inventory: dict, project_id: str) -> str:
    lines = _import_header("DNS and related resources")
    for item in inventory.get("dns_managed_zones", []):
        _add_import(
            lines,
            f"module.dns.google_dns_managed_zone.this[{_hcl_string(item['name'])}]",
            f"projects/{_project(item, project_id)}/managedZones/{_source_name(item)}",
        )

    for item in inventory.get("dns_record_sets", []):
        zone = item.get("managed_zone")
        record_name = (item.get("attributes") or {}).get("name") or item.get("dns_name") or _source_name(item)
        record_type = item.get("record_type")
        if not zone or not record_type:
            lines.extend([
                f"# Skipping DNS record import for {_source_name(item)}: missing managed_zone or record_type.",
                "",
            ])
            continue
        _add_import(
            lines,
            f"module.dns.google_dns_record_set.this[{_hcl_string(item['name'])}]",
            (
                f"projects/{_project(item, project_id)}/managedZones/{zone}"
                f"/rrsets/{record_name}/{record_type}"
            ),
        )
    return _finish_imports(lines)


def _add_address_import(lines: list[str], item: dict, project_id: str) -> None:
    if item.get("scope") == "global":
        to_address = "module.address.google_compute_global_address.this"
        import_id = f"projects/{_project(item, project_id)}/global/addresses/{_source_name(item)}"
    else:
        to_address = "module.address.google_compute_address.this"
        import_id = (
            f"projects/{_project(item, project_id)}/regions/{item['region']}"
            f"/addresses/{_source_name(item)}"
        )
    _add_import(lines, f"{to_address}[{_hcl_string(item['name'])}]", import_id)


def _router_child_import_id(item: dict, project_id: str) -> str:
    return (
        f"projects/{_project(item, project_id)}/regions/{item['region']}"
        f"/routers/{item.get('router_source_name') or item.get('router')}/{_source_name(item)}"
    )


def _network_import_name(item: dict) -> str:
    attrs = item.get("attributes") or {}
    network = attrs.get("network") or attrs.get("networkUrl") or attrs.get("network_url")
    if isinstance(network, dict):
        network = network.get("name") or network.get("selfLink") or network.get("self_link")
    if network:
        return str(network).rstrip("/").split("/")[-1]
    return str(item.get("parent_vpc") or _source_name(item))


def _import_header(title: str) -> list[str]:
    return [
        f"# imports.tf - {title}.",
        "# Generated from inventory. Review module addresses before applying imports.",
        "",
    ]


def _finish_imports(lines: list[str]) -> str:
    if len(lines) == 3:
        lines.append("# No resources were found for this group.")
    return "\n".join(lines).rstrip() + "\n"


def _by_vpc(items: list, vpc_name: str | None, known_vpcs: set[str] | None = None) -> list:
    if vpc_name is None:
        return [
            item for item in items
            if item.get("parent_vpc") and item.get("parent_vpc") not in (known_vpcs or set())
        ]
    return [item for item in items if item.get("parent_vpc") == vpc_name]


def _by_router(items: list, router_names: set[str]) -> list:
    return [item for item in items if item.get("router") in router_names]


def _has_unassigned_vpc_import_items(inventory: dict, known_vpcs: set[str]) -> bool:
    for key in (
        "subnets",
        "addresses",
        "service_networking_connections",
        "interconnect_attachments",
    ):
        if _by_vpc(inventory.get(key, []), None, known_vpcs):
            return True
    return any(inventory.get(key) for key in (
        "shared_vpc_host_projects",
        "shared_vpc_service_projects",
        "iam_partial_policies",
    ))


def _add_import(lines: list[str], to_address: str, import_id: str) -> None:
    lines.extend([
        "import {",
        f"  to = {to_address}",
        f"  id = {_hcl_string(import_id)}",
        "}",
        "",
    ])


def _hcl_string(value: str) -> str:
    return json.dumps(str(value))


def _project(item: dict, default_project_id: str) -> str:
    return str(item.get("project") or default_project_id)


def _source_name(item: dict) -> str:
    return str(item.get("source_name") or item["name"])


def _clean_generated_group_dirs(output_dir: Path) -> None:
    for dirname in ("tfvars", "resource_groups"):
        path = output_dir / dirname
        if path.exists() and path.is_dir():
            try:
                shutil.rmtree(path)
            except OSError as exc:
                print(f"    Could not remove stale generated folder {path}: {exc}")


def _w(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")
    print(f"    ✅  {path.relative_to(OUTPUT_DIR)}")
