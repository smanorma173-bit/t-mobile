"""
inventory/loader.py
Parses GCP asset inventory → normalised dict for Terraform generation.

Supports two input modes:
  1. CSV file path (str | Path) — legacy mode, same as before.
  2. List of row dicts — produced by gcp_fetcher.fetch() for live API mode.

Excluded:
  compute.Route — all routes in this project are GCP-managed system routes
  (default-route-* / peering-route-*). They have no usable next_hop data
  and cannot be represented as google_compute_route resources.
"""

import re
import json
import pandas as pd
from pathlib import Path

NETWORK_TYPES = {
    "compute.Network",
    "compute.Subnetwork",
    "compute.Firewall",
    "compute.Router",
    "compute.RouterNat",
    "compute.RouterInterface",
    "compute.RouterPeer",
    "compute.Address",
    "compute.Project",
    "compute.InterconnectAttachment",
    "compute.NetworkEndpointGroup",
    "compute.SharedVPCHostProject",
    "compute.SharedVPCServiceProject",
    "serviceNetworking.Connection",
    "dns.ManagedZone",
    "dns.ResourceRecordSet",
    "iam.PartialPolicy",
}

VPC_TFVARS_KEYS = [
    "vpcs",
    "subnets",
    "addresses",
    "router_nats",
    "service_networking_connections",
    "shared_vpc_host_projects",
    "routers",
    "interconnect_attachments",
    "router_interfaces",
    "router_peers",
    "shared_vpc_service_projects",
    "iam_partial_policies",
]


def load(source: "str | Path | list[dict]") -> dict:
    """
    Accept either:
      - a CSV file path  → parse with pandas
      - a list of dicts  → already normalised rows from gcp_fetcher

    Returns the standard inventory dict.
    """
    if isinstance(source, list):
        rows_df = pd.DataFrame(source)
    else:
        df = pd.read_csv(source)
        rows_df = df[df["Resource type"].isin(NETWORK_TYPES)].copy()

    rows_df = rows_df[rows_df["Resource type"].isin(NETWORK_TYPES)].copy()

    project_id = (
        rows_df["Project Id"].dropna().iloc[0]
        if not rows_df["Project Id"].dropna().empty
        else "my-project"
    )

    vpcs:      list = []
    subnets:   list = []
    firewalls: list = []
    routers:   list = []
    addresses: list = []
    negs:      list = []
    router_nats: list = []
    service_networking_connections: list = []
    shared_vpc_host_projects: list = []
    interconnect_attachments: list = []
    router_interfaces: list = []
    router_peers: list = []
    shared_vpc_service_projects: list = []
    iam_partial_policies: list = []
    dns_managed_zones: list = []
    dns_record_sets: list = []

    seen: dict[str, set] = {k: set() for k in
                            [
                                "vpc", "subnet", "firewall", "router", "address", "neg",
                                "router_nat", "service_networking_connection",
                                "shared_vpc_host_project", "interconnect_attachment",
                                "router_interface", "router_peer",
                                "shared_vpc_service_project", "iam_partial_policy",
                                "dns_managed_zone", "dns_record_set",
                            ]}

    for _, row in rows_df.iterrows():
        base  = _name(str(row["Name"]))
        rtype = row["Resource type"]
        loc   = str(row.get("Location", "global")).strip()
        attrs = _parse_attrs(row.get("Additional attributes", ""))
        proj  = str(row.get("Project Id", "")).strip()
        if rtype == "compute.Network":
            key = _key(base, seen["vpc"])
            vpcs.append({
                "name": key,
                "source_name": base,
                "project": proj,
                "auto_create_subnetworks": _optional_bool(attrs.get("autoCreateSubnetworks")),
                "description": attrs.get("description") or None,
                "routing_mode": (
                    attrs.get("routingMode")
                    or attrs.get("routing_mode")
                    or (attrs.get("routingConfig") or {}).get("routingMode")
                    or None
                ),
                "subnetworks": attrs.get("subnetworks") if isinstance(attrs.get("subnetworks"), list) else [],
            })

        elif rtype == "compute.Subnetwork":
            cidr = attrs.get("ipCidrRange") or attrs.get("ip_cidr_range")
            if not cidr:
                print(
                    f"  Skipping subnet {base}: missing ipCidrRange in Additional attributes."
                )
                continue
            key  = _key(base, seen["subnet"], loc)
            subnets.append({
                "name": key, "project": proj, "region": loc,
                "source_name": base,
                "ip_cidr_range": str(cidr),
                "parent_vpc": _parent(base, vpcs, attrs),
                "private_ip_google_access": _optional_bool(attrs.get("privateIpGoogleAccess")),
                "secondary_ip_ranges": _secondary_ip_ranges(attrs),
                "stack_type": _stack_type(attrs.get("stackType") or attrs.get("stack_type")),
                "log_config": _subnet_log_config(attrs),
            })

        elif rtype == "compute.Firewall":
            key = _key(base, seen["firewall"])
            allow_rules = _extract_fw_rules(attrs, "allowed")
            deny_rules  = _extract_fw_rules(attrs, "denied")
            direction   = str(attrs.get("direction", "INGRESS")).upper()
            src_ranges  = attrs.get("sourceRanges") or attrs.get("source_ranges") or None
            dst_ranges  = attrs.get("destinationRanges") or attrs.get("destination_ranges") or None
            src_tags    = attrs.get("sourceTags") or attrs.get("source_tags") or None
            src_svc_accts = attrs.get("sourceServiceAccounts") or attrs.get("source_service_accounts") or None
            tgt_tags    = attrs.get("targetTags") or attrs.get("target_tags") or None
            tgt_svc_accts = attrs.get("targetServiceAccounts") or attrs.get("target_service_accounts") or None
            log_config = attrs.get("logConfig") or attrs.get("log_config") or None
            firewalls.append({
                "name":          key,
                "source_name":   base,
                "project":       proj,
                "parent_vpc":    _parent(base, vpcs, attrs),
                "direction":     direction,
                "priority":      _optional_int(attrs.get("priority")),
                "description":   attrs.get("description") or None,
                "disabled":      _optional_bool(attrs.get("disabled")),
                "allow":         allow_rules,
                "deny":          deny_rules,
                "source_ranges": src_ranges if isinstance(src_ranges, list) else None,
                "destination_ranges": dst_ranges if isinstance(dst_ranges, list) else None,
                "source_tags":   src_tags if isinstance(src_tags, list) else None,
                "source_service_accounts": src_svc_accts if isinstance(src_svc_accts, list) else None,
                "target_tags":   tgt_tags if isinstance(tgt_tags, list) else None,
                "target_service_accounts": tgt_svc_accts if isinstance(tgt_svc_accts, list) else None,
                "log_config":    log_config if isinstance(log_config, dict) else None,
            })

        elif rtype == "compute.Router":
            key = _key(base, seen["router"], loc)
            routers.append({
                "name": key, "project": proj, "region": loc,
                "source_name": base,
                "parent_vpc": _parent(base, vpcs, attrs),
                "attributes": _clean_attrs(attrs),
            })
            router_nats.extend(_router_children(
                attrs, "nats", seen["router_nat"], key, base, proj, loc, "router_nat"
            ))
            router_interfaces.extend(_router_children(
                attrs, "interfaces", seen["router_interface"], key, base, proj, loc, "router_interface"
            ))
            router_peers.extend(_router_children(
                attrs, "bgpPeers", seen["router_peer"], key, base, proj, loc, "router_peer"
            ))

        elif rtype == "compute.Address":
            scope = "global" if loc == "global" else "regional"
            key   = _key(base, seen["address"], loc)
            addresses.append({
                "name": key, "project": proj, "region": loc, "scope": scope,
                "source_name": base,
                "address": _optional_str(attrs.get("address")),
                "description": _optional_str(attrs.get("description")),
                "address_type": _optional_str(attrs.get("addressType") or attrs.get("address_type")),
                "network_tier": _optional_str(attrs.get("networkTier") or attrs.get("network_tier")),
                "parent_vpc": _parent(base, vpcs, attrs),
                "attributes": _clean_attrs(attrs),
            })

        elif rtype == "compute.InterconnectAttachment":
            key = _key(base, seen["interconnect_attachment"], loc)
            interconnect_attachments.append(_generic_network_item(
                key, base, proj, loc, attrs, vpcs
            ))

        elif rtype == "compute.NetworkEndpointGroup":
            # Skip GKE/GCP auto-managed NEGs — they are created and deleted
            # automatically and must not be managed by Terraform.
            # Patterns: k8s1-*, k8s2-*, gke-*
            if _is_auto_neg(base):
                continue
            key = _key(base, seen["neg"], loc)
            negs.append({
                "name":       key,
                "source_name": base,
                "project":    proj,
                "zone":       loc,
                "parent_vpc": _parent(base, vpcs, attrs),
            })

        elif rtype == "serviceNetworking.Connection":
            key = _key(base, seen["service_networking_connection"], loc)
            service_networking_connections.append(_generic_network_item(
                key, base, proj, loc, attrs, vpcs
            ))

        elif rtype == "compute.SharedVPCHostProject":
            key = _key(base, seen["shared_vpc_host_project"], loc)
            shared_vpc_host_projects.append(_generic_project_item(key, base, proj, loc, attrs))

        elif rtype == "compute.SharedVPCServiceProject":
            key = _key(base, seen["shared_vpc_service_project"], loc)
            shared_vpc_service_projects.append(_generic_project_item(key, base, proj, loc, attrs))

        elif rtype == "compute.Project":
            status = str(attrs.get("xpnProjectStatus") or "").upper()
            if status == "HOST":
                key = _key(base, seen["shared_vpc_host_project"], loc)
                shared_vpc_host_projects.append(_generic_project_item(key, base, proj, loc, attrs))
            else:
                key = _key(base, seen["shared_vpc_service_project"], loc)
                shared_vpc_service_projects.append(_generic_project_item(key, base, proj, loc, attrs))

        elif rtype == "iam.PartialPolicy":
            key = _key(base, seen["iam_partial_policy"], loc)
            iam_partial_policies.append(_generic_project_item(key, base, proj, loc, attrs))

        elif rtype == "dns.ManagedZone":
            key = _key(base, seen["dns_managed_zone"], loc)
            dns_managed_zones.append(_dns_item(key, base, proj, loc, attrs))

        elif rtype == "dns.ResourceRecordSet":
            key = _key(base, seen["dns_record_set"], loc)
            dns_record_sets.append(_dns_item(key, base, proj, loc, attrs))

    return {
        "project_id": project_id,
        "vpcs":       vpcs,
        "subnets":    subnets,
        "firewalls":  firewalls,
        "routers":    routers,
        "addresses":  addresses,
        "negs":       negs,
        "router_nats": router_nats,
        "service_networking_connections": service_networking_connections,
        "shared_vpc_host_projects": shared_vpc_host_projects,
        "interconnect_attachments": interconnect_attachments,
        "router_interfaces": router_interfaces,
        "router_peers": router_peers,
        "shared_vpc_service_projects": shared_vpc_service_projects,
        "iam_partial_policies": iam_partial_policies,
        "dns_managed_zones": dns_managed_zones,
        "dns_record_sets": dns_record_sets,
    }


# ── Helpers ────────────────────────────────────────────────────────────────

def _name(full: str) -> str:
    return full.strip().split("/")[-1]


def _parse_attrs(raw) -> dict:
    if pd.isna(raw) if not isinstance(raw, str) else not raw:
        return {}
    try:
        s = str(raw).strip()
        if s.startswith("{"):
            try:
                return json.loads(s)
            except Exception:
                pass
        s = re.sub(r'(\w+):', r'"\1":', s)
        s = re.sub(r':(\w[\w.]*)', r':"\1"', s)
        return json.loads(s)
    except Exception:
        return {}


def _sanitise(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9\-]", "-", name).strip("-")
    if s and s[0].isdigit():
        s = "r-" + s
    return s or "resource"


def _key(base: str, seen: set, disambiguator: str = "") -> str:
    candidate = _sanitise(base)
    if candidate not in seen:
        seen.add(candidate)
        return candidate
    if disambiguator:
        slug = _sanitise(disambiguator)
        alt  = f"{candidate}-{slug}"
        if alt not in seen:
            seen.add(alt)
            return alt
    idx = 2
    while True:
        alt = f"{candidate}-{idx}"
        if alt not in seen:
            seen.add(alt)
            return alt
        idx += 1


def _as_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    if value is None:
        return default
    return bool(value)


def _optional_bool(value) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _optional_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _as_float(value, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _secondary_ip_ranges(attrs: dict) -> list:
    raw = attrs.get("secondaryIpRanges") or attrs.get("secondary_ip_ranges") or []
    if not isinstance(raw, list):
        return []
    ranges = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        range_name = entry.get("rangeName") or entry.get("range_name")
        ip_cidr_range = entry.get("ipCidrRange") or entry.get("ip_cidr_range")
        if range_name and ip_cidr_range:
            ranges.append({
                "range_name": str(range_name),
                "ip_cidr_range": str(ip_cidr_range),
            })
    return ranges


def _subnet_log_config(attrs: dict) -> dict | None:
    raw = attrs.get("logConfig") or attrs.get("log_config")
    if not isinstance(raw, dict):
        return {"metadata": "INCLUDE_ALL_METADATA"} if _as_bool(attrs.get("enableFlowLogs"), False) else None

    cfg = {
        "aggregation_interval": raw.get("aggregationInterval") or raw.get("aggregation_interval"),
        "flow_sampling": _as_float(raw.get("flowSampling") or raw.get("flow_sampling")),
        "metadata": raw.get("metadata"),
        "metadata_fields": raw.get("metadataFields") or raw.get("metadata_fields"),
        "filter_expr": raw.get("filterExpr") or raw.get("filter_expr"),
    }
    return {k: v for k, v in cfg.items() if v is not None}


def _stack_type(value) -> str | None:
    allowed = {"IPV4_ONLY", "IPV4_IPV6", "IPV6_ONLY"}
    value = str(value or "").strip().upper()
    return value if value in allowed else None


def _parent(resource_name: str, vpcs: list, attrs: dict | None = None) -> str:
    vpc_names = [v["name"] if isinstance(v, dict) else v for v in vpcs]
    explicit_network = _network_name(attrs or {})
    if explicit_network:
        return _matching_vpc_name(explicit_network, vpcs) or explicit_network

    for vpc in vpcs:
        if not isinstance(vpc, dict):
            continue
        for subnet_self_link in vpc.get("subnetworks", []):
            if str(subnet_self_link).split("/")[-1] == resource_name:
                return vpc["name"]

    if "default" in vpc_names:
        return "default"
    return vpc_names[0] if len(vpc_names) == 1 else "default"


def _network_name(attrs: dict) -> str:
    for key in ("network", "networkUrl", "network_url", "parentNetwork"):
        value = attrs.get(key)
        if value:
            if isinstance(value, dict):
                value = value.get("name") or value.get("selfLink") or value.get("self_link")
            return str(value).rstrip("/").split("/")[-1]
    return ""


def _matching_vpc_name(network_name: str, vpcs: list) -> str:
    for vpc in vpcs:
        if not isinstance(vpc, dict):
            if network_name == vpc:
                return vpc
            continue
        if network_name in {vpc.get("source_name"), vpc.get("name")}:
            return vpc["name"]
    return ""


def _extract_fw_rules(attrs: dict, key: str) -> list:
    """
    Extract allow/deny rules from Cloud Asset Inventory firewall data.
    `key` is "allowed" or "denied".
    Returns list of {protocol, ports} dicts, or [] if none.
    """
    raw = attrs.get(key, [])
    if not isinstance(raw, list):
        return []
    rules = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        proto = str(entry.get("IPProtocol") or entry.get("protocol") or "all")
        ports = entry.get("ports", [])
        rules.append({"protocol": proto, "ports": ports if isinstance(ports, list) else []})
    return rules


def _generic_network_item(
    key: str,
    source_name: str,
    project: str,
    location: str,
    attrs: dict,
    vpcs: list,
) -> dict:
    return {
        "name": key,
        "source_name": source_name,
        "project": project,
        "location": location,
        "parent_vpc": _parent(source_name, vpcs, attrs),
        "attributes": _clean_attrs(attrs),
    }


def _generic_project_item(
    key: str,
    source_name: str,
    project: str,
    location: str,
    attrs: dict,
) -> dict:
    return {
        "name": key,
        "source_name": source_name,
        "project": project,
        "location": location,
        "attributes": _clean_attrs(attrs),
    }


def _dns_item(
    key: str,
    source_name: str,
    project: str,
    location: str,
    attrs: dict,
) -> dict:
    return {
        "name": key,
        "source_name": source_name,
        "project": project,
        "location": location,
        "dns_name": attrs.get("dnsName") or attrs.get("dns_name"),
        "record_type": attrs.get("type"),
        "ttl": _optional_int(attrs.get("ttl")),
        "rrdatas": _dns_rrdatas(attrs),
        "managed_zone": _managed_zone_name(attrs),
        "attributes": _clean_attrs(attrs),
    }


def _router_children(
    attrs: dict,
    child_key: str,
    seen: set,
    router_key: str,
    router_source_name: str,
    project: str,
    region: str,
    item_type: str,
) -> list:
    raw_items = attrs.get(child_key) or []
    if not isinstance(raw_items, list):
        return []

    children = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        child_name = item.get("name") or item.get("peerName") or item.get("interfaceName")
        if not child_name:
            child_name = f"{router_source_name}-{item_type}-{len(children) + 1}"
        key = _key(f"{router_source_name}-{child_name}", seen, region)
        children.append({
            "name": key,
            "source_name": str(child_name),
            "project": project,
            "region": region,
            "router": router_key,
            "router_source_name": router_source_name,
            "attributes": _clean_attrs(item),
        })
    return children


def _managed_zone_name(attrs: dict) -> str | None:
    for key in ("managedZone", "managed_zone", "zone", "zoneName", "managedZoneName"):
        value = attrs.get(key)
        if value:
            return str(value).rstrip("/").split("/")[-1]
    return None


def _dns_rrdatas(attrs: dict) -> list | None:
    values = attrs.get("rrdatas")
    if values is None:
        values = attrs.get("rrdata")
    if values is None:
        return None
    if isinstance(values, list):
        return values
    return [str(values)]


def _clean_attrs(value):
    if isinstance(value, dict):
        return {
            str(k): _clean_attrs(v)
            for k, v in value.items()
            if v is not None
        }
    if isinstance(value, list):
        return [_clean_attrs(v) for v in value if v is not None]
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value) if value is not None else None


def _is_auto_neg(name: str) -> bool:
    """
    Returns True for NEGs that are auto-created and auto-deleted by GKE/GCP.
    These must NOT be managed by Terraform.
    """
    prefixes = ("k8s1-", "k8s2-", "gke-")
    return any(name.lower().startswith(p) for p in prefixes)
