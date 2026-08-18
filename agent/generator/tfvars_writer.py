"""
generator/tfvars_writer.py
Writes terraform.tfvars deterministically from the inventory dict.
No LLM involved; pure Python string formatting guarantees valid HCL.
"""

import json


def generate(inventory: dict) -> str:
    """Return the full content of terraform.tfvars as a string."""
    lines = []
    pid = inventory["project_id"]

    lines += [f'project_id = "{pid}"', 'region     = "us-central1"', ""]

    lines += _map_block("vpcs",       inventory["vpcs"],       _vpc_obj)
    lines += _map_block("subnets",    inventory["subnets"],    _subnet_obj)
    lines += _map_block("firewalls",  inventory["firewalls"],  _firewall_obj)
    lines += _map_block("routers",    inventory["routers"],    _router_obj)
    lines += _map_block("addresses",  inventory["addresses"],  _address_obj)
    lines += _map_block("negs",       inventory["negs"],       _neg_obj)
    lines += _map_block("router_nats", inventory.get("router_nats", []), _generic_obj)
    lines += _map_block(
        "service_networking_connections",
        inventory.get("service_networking_connections", []),
        _generic_obj,
    )
    lines += _map_block(
        "shared_vpc_host_projects",
        inventory.get("shared_vpc_host_projects", []),
        _generic_obj,
    )
    lines += _map_block(
        "interconnect_attachments",
        inventory.get("interconnect_attachments", []),
        _generic_obj,
    )
    lines += _map_block(
        "router_interfaces",
        inventory.get("router_interfaces", []),
        _generic_obj,
    )
    lines += _map_block("router_peers", inventory.get("router_peers", []), _generic_obj)
    lines += _map_block(
        "shared_vpc_service_projects",
        inventory.get("shared_vpc_service_projects", []),
        _generic_obj,
    )
    lines += _map_block(
        "iam_partial_policies",
        inventory.get("iam_partial_policies", []),
        _generic_obj,
    )
    lines += _map_block(
        "dns_managed_zones",
        inventory.get("dns_managed_zones", []),
        _dns_obj,
    )
    lines += _map_block(
        "dns_record_sets",
        inventory.get("dns_record_sets", []),
        _dns_obj,
    )

    return "\n".join(lines)


def generate_split(inventory: dict) -> dict[str, str]:
    """Return resource-group tfvars files keyed by relative output path."""
    files: dict[str, str] = {}

    known_vpcs = {v["name"] for v in inventory.get("vpcs", [])}
    for vpc in inventory.get("vpcs", []):
        files[f"resource_groups/vpc-{vpc['name']}/terraform.tfvars"] = _vpc_tfvars(inventory, vpc["name"])

    if _has_unassigned_vpc_items(inventory, known_vpcs):
        files["resource_groups/vpc-unassigned/terraform.tfvars"] = _vpc_tfvars(inventory, None, known_vpcs)

    files["resource_groups/firewall/terraform.tfvars"] = _domain_tfvars(
        inventory,
        [("firewalls", inventory.get("firewalls", []), _firewall_obj)],
    )
    files["resource_groups/dns/terraform.tfvars"] = _domain_tfvars(
        inventory,
        [
            ("dns_managed_zones", inventory.get("dns_managed_zones", []), _dns_obj),
            ("dns_record_sets", inventory.get("dns_record_sets", []), _dns_obj),
        ],
    )
    return files


def _vpc_tfvars(
    inventory: dict,
    vpc_name: str | None,
    known_vpcs: set[str] | None = None,
) -> str:
    routers = _by_vpc(inventory.get("routers", []), vpc_name, known_vpcs)
    router_names = {item["name"] for item in routers}
    sections = [
        ("vpcs", [v for v in inventory.get("vpcs", []) if vpc_name and v["name"] == vpc_name], _vpc_obj),
        ("subnets", _by_vpc(inventory.get("subnets", []), vpc_name, known_vpcs), _subnet_obj),
        ("addresses", _by_vpc(inventory.get("addresses", []), vpc_name, known_vpcs), _address_detail_obj),
        ("router_nats", _by_router(inventory.get("router_nats", []), router_names), _generic_obj),
        (
            "service_networking_connections",
            _by_vpc(inventory.get("service_networking_connections", []), vpc_name, known_vpcs),
            _generic_obj,
        ),
        (
            "shared_vpc_host_projects",
            inventory.get("shared_vpc_host_projects", []) if vpc_name is None else [],
            _generic_obj,
        ),
        ("routers", routers, _router_detail_obj),
        (
            "interconnect_attachments",
            _by_vpc(inventory.get("interconnect_attachments", []), vpc_name, known_vpcs),
            _generic_obj,
        ),
        ("router_interfaces", _by_router(inventory.get("router_interfaces", []), router_names), _generic_obj),
        ("router_peers", _by_router(inventory.get("router_peers", []), router_names), _generic_obj),
        (
            "shared_vpc_service_projects",
            inventory.get("shared_vpc_service_projects", []) if vpc_name is None else [],
            _generic_obj,
        ),
        (
            "iam_partial_policies",
            inventory.get("iam_partial_policies", []) if vpc_name is None else [],
            _generic_obj,
        ),
    ]
    return _domain_tfvars(inventory, sections)


def _domain_tfvars(inventory: dict, sections: list[tuple[str, list, object]]) -> str:
    lines = [
        f'project_id = "{inventory["project_id"]}"',
        'region     = "us-central1"',
        "",
    ]
    for name, items, formatter in sections:
        lines += _map_block(name, items, formatter)
    return "\n".join(lines)


def _by_vpc(items: list, vpc_name: str | None, known_vpcs: set[str] | None = None) -> list:
    if vpc_name is None:
        return [
            item for item in items
            if item.get("parent_vpc") and item.get("parent_vpc") not in (known_vpcs or set())
        ]
    return [item for item in items if item.get("parent_vpc") == vpc_name]


def _by_router(items: list, router_names: set[str]) -> list:
    return [item for item in items if item.get("router") in router_names]


def _has_unassigned_vpc_items(inventory: dict, known_vpcs: set[str]) -> bool:
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


def _map_block(var_name: str, items: list, formatter) -> list:
    if not items:
        return [f"{var_name} = {{}}", ""]
    lines = [f"{var_name} = {{"]
    for item in items:
        key = item["name"]
        lines.append(f"  {key} = {{")
        lines += formatter(item)
        lines.append("  }")
    lines += ["}", ""]
    return lines


def _vpc_obj(v: dict) -> list:
    lines = [
        f'    project                 = "{v["project"]}"',
    ]
    if v.get("auto_create_subnetworks") is not None:
        lines.append(f'    auto_create_subnetworks = {_bool(v["auto_create_subnetworks"])}')
    if v.get("description"):
        lines.append(f'    description             = {json.dumps(v["description"])}')
    if v.get("routing_mode"):
        lines.append(f'    routing_mode            = {json.dumps(v["routing_mode"])}')
    return lines


def _subnet_obj(v: dict) -> list:
    lines = [
        f'    project       = "{v["project"]}"',
        f'    region        = "{v["region"]}"',
        f'    ip_cidr_range = "{v["ip_cidr_range"]}"',
        f'    parent_vpc    = "{v["parent_vpc"]}"',
    ]
    if v.get("private_ip_google_access") is not None:
        lines.append(f'    private_ip_google_access = {_bool(v["private_ip_google_access"])}')
    stack_type = _stack_type(v.get("stack_type"))
    if stack_type:
        lines.append(f'    stack_type = {json.dumps(stack_type)}')
    _append_secondary_ranges(lines, v.get("secondary_ip_ranges"))
    _append_subnet_log_config(lines, v.get("log_config"))
    return lines


def _firewall_obj(v: dict) -> list:
    lines = [
        f'    project    = "{v["project"]}"',
        f'    parent_vpc = "{v["parent_vpc"]}"',
        f'    direction  = "{v.get("direction", "INGRESS")}"',
    ]
    priority = _optional_int(v.get("priority"))
    if priority is not None:
        lines.append(f'    priority   = {priority}')

    if v.get("description"):
        lines.append(f'    description = {json.dumps(v["description"])}')
    if v.get("disabled") is not None:
        lines.append(f'    disabled    = {_bool(v["disabled"])}')

    _append_string_list(lines, "source_ranges", v.get("source_ranges"))
    _append_string_list(lines, "destination_ranges", v.get("destination_ranges"))
    _append_string_list(lines, "source_tags", v.get("source_tags"))
    _append_string_list(lines, "source_service_accounts", v.get("source_service_accounts"))
    _append_string_list(lines, "target_tags", v.get("target_tags"))
    _append_string_list(lines, "target_service_accounts", v.get("target_service_accounts"))
    _append_log_config(lines, v.get("log_config"))

    allow = v.get("allow", [])
    if allow:
        lines.append("    allow = [")
        for rule in allow:
            proto = rule.get("protocol", "all")
            ports = rule.get("ports", [])
            ports_hcl = ", ".join(f'"{p}"' for p in ports)
            lines.append("      {")
            lines.append(f'        protocol = "{proto}"')
            lines.append(f'        ports    = [{ports_hcl}]')
            lines.append("      },")
        lines.append("    ]")
    else:
        lines.append("    allow = []")

    deny = v.get("deny", [])
    if deny:
        lines.append("    deny = [")
        for rule in deny:
            proto = rule.get("protocol", "all")
            ports = rule.get("ports", [])
            ports_hcl = ", ".join(f'"{p}"' for p in ports)
            lines.append("      {")
            lines.append(f'        protocol = "{proto}"')
            lines.append(f'        ports    = [{ports_hcl}]')
            lines.append("      },")
        lines.append("    ]")
    else:
        lines.append("    deny = []")

    return lines


def _append_string_list(lines: list, key: str, values) -> None:
    if values and isinstance(values, list):
        quoted = ", ".join(json.dumps(str(value)) for value in values)
        lines.append(f"    {key} = [{quoted}]")


def _append_log_config(lines: list, log_config) -> None:
    if not isinstance(log_config, dict):
        return
    metadata = log_config.get("metadata")
    if not metadata:
        return
    lines.append("    log_config = {")
    lines.append(f"      metadata = {json.dumps(str(metadata))}")
    lines.append("    }")


def _append_secondary_ranges(lines: list, ranges) -> None:
    if not ranges:
        return
    lines.append("    secondary_ip_ranges = [")
    for item in ranges:
        if not isinstance(item, dict):
            continue
        lines.append("      {")
        lines.append(f'        range_name    = {json.dumps(str(item["range_name"]))}')
        lines.append(f'        ip_cidr_range = {json.dumps(str(item["ip_cidr_range"]))}')
        lines.append("      },")
    lines.append("    ]")


def _append_subnet_log_config(lines: list, log_config) -> None:
    if not isinstance(log_config, dict):
        return
    lines.append("    log_config = {")
    _append_optional_string(lines, "aggregation_interval", log_config.get("aggregation_interval"), indent="      ")
    if log_config.get("flow_sampling") is not None:
        lines.append(f'      flow_sampling = {log_config["flow_sampling"]}')
    _append_optional_string(lines, "metadata", log_config.get("metadata"), indent="      ")
    _append_optional_string_list(lines, "metadata_fields", log_config.get("metadata_fields"), indent="      ")
    _append_optional_string(lines, "filter_expr", log_config.get("filter_expr"), indent="      ")
    lines.append("    }")


def _append_optional_string(lines: list, key: str, value, indent: str = "    ") -> None:
    if value:
        lines.append(f"{indent}{key} = {json.dumps(str(value))}")


def _append_optional_string_list(lines: list, key: str, values, indent: str = "    ") -> None:
    if values and isinstance(values, list):
        quoted = ", ".join(json.dumps(str(value)) for value in values)
        lines.append(f"{indent}{key} = [{quoted}]")


def _int(value, default: int) -> int:
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


def _bool(value) -> str:
    return str(bool(value)).lower()


def _stack_type(value) -> str | None:
    allowed = {"IPV4_ONLY", "IPV4_IPV6", "IPV6_ONLY"}
    value = str(value or "").strip().upper()
    return value if value in allowed else None


def _router_obj(v: dict) -> list:
    return [
        f'    project    = "{v["project"]}"',
        f'    region     = "{v["region"]}"',
        f'    parent_vpc = "{v["parent_vpc"]}"',
    ]


def _address_obj(v: dict) -> list:
    lines = [
        f'    project = "{v["project"]}"',
        f'    region  = "{v["region"]}"',
        f'    scope   = "{v["scope"]}"',
    ]
    if v.get("address"):
        lines.append(f'    address = "{v["address"]}"')
    if v.get("description"):
        lines.append(f'    description = {json.dumps(str(v["description"]))}')
    if v.get("address_type"):
        lines.append(f'    address_type = {json.dumps(str(v["address_type"]))}')
    if v.get("network_tier"):
        lines.append(f'    network_tier = {json.dumps(str(v["network_tier"]))}')
    return lines


def _address_detail_obj(v: dict) -> list:
    lines = _address_obj(v)
    if v.get("parent_vpc"):
        lines.append(f'    parent_vpc = {json.dumps(str(v["parent_vpc"]))}')
    if v.get("source_name"):
        lines.append(f'    source_name = {json.dumps(str(v["source_name"]))}')
    if v.get("attributes"):
        lines.append(f"    attributes = {_hcl_value(v['attributes'], 4)}")
    return lines


def _router_detail_obj(v: dict) -> list:
    lines = _router_obj(v)
    if v.get("source_name"):
        lines.append(f'    source_name = {json.dumps(str(v["source_name"]))}')
    if v.get("attributes"):
        lines.append(f"    attributes  = {_hcl_value(v['attributes'], 4)}")
    return lines


def _neg_obj(v: dict) -> list:
    return [
        f'    project    = "{v["project"]}"',
        f'    zone       = "{v["zone"]}"',
        f'    parent_vpc = "{v["parent_vpc"]}"',
    ]


def _generic_obj(v: dict) -> list:
    lines = [
        f'    project     = {json.dumps(str(v.get("project", "")))}',
    ]
    if v.get("location"):
        lines.append(f'    location    = {json.dumps(str(v["location"]))}')
    if v.get("region"):
        lines.append(f'    region      = {json.dumps(str(v["region"]))}')
    if v.get("parent_vpc"):
        lines.append(f'    parent_vpc  = {json.dumps(str(v["parent_vpc"]))}')
    if v.get("router"):
        lines.append(f'    router      = {json.dumps(str(v["router"]))}')
    if v.get("source_name"):
        lines.append(f'    source_name = {json.dumps(str(v["source_name"]))}')
    attrs = v.get("attributes")
    if attrs:
        lines.append(f"    attributes  = {_hcl_value(attrs, 4)}")
    return lines


def _dns_obj(v: dict) -> list:
    lines = [
        f'    project     = {json.dumps(str(v.get("project", "")))}',
        f'    location    = {json.dumps(str(v.get("location", "global")))}',
    ]
    if v.get("source_name"):
        lines.append(f'    source_name = {json.dumps(str(v["source_name"]))}')
    if v.get("dns_name"):
        lines.append(f'    dns_name    = {json.dumps(str(v["dns_name"]))}')
    if v.get("managed_zone"):
        lines.append(f'    managed_zone = {json.dumps(str(v["managed_zone"]))}')
    if v.get("record_type"):
        lines.append(f'    record_type = {json.dumps(str(v["record_type"]))}')
    if v.get("ttl") is not None:
        lines.append(f'    ttl         = {_optional_int(v.get("ttl"))}')
    _append_string_list(lines, "rrdatas", v.get("rrdatas"))
    attrs = v.get("attributes")
    if attrs:
        lines.append(f"    attributes  = {_hcl_value(attrs, 4)}")
    return lines


def _hcl_value(value, indent: int = 0) -> str:
    space = " " * indent
    child_space = " " * (indent + 2)
    if isinstance(value, bool):
        return _bool(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str) or value is None:
        return "null" if value is None else json.dumps(value)
    if isinstance(value, list):
        if not value:
            return "[]"
        rendered = [f"{child_space}{_hcl_value(item, indent + 2)}," for item in value]
        return "[\n" + "\n".join(rendered) + f"\n{space}]"
    if isinstance(value, dict):
        if not value:
            return "{}"
        rendered = [
            f"{child_space}{_hcl_key(key)} = {_hcl_value(item, indent + 2)}"
            for key, item in sorted(value.items())
            if item is not None
        ]
        return "{\n" + "\n".join(rendered) + f"\n{space}}}"
    return json.dumps(str(value))


def _hcl_key(value: str) -> str:
    value = str(value)
    if value.replace("_", "").replace("-", "").isalnum() and not value[:1].isdigit():
        return value
    return json.dumps(value)


__all__ = ["generate", "generate_split"]

