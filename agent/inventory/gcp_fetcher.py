"""
inventory/gcp_fetcher.py
Fetches GCP network resources directly from the Cloud Asset Inventory API
using the `gcloud` CLI. No CSV/Excel upload required — just pass --project.

Requirements:
  - gcloud CLI installed and authenticated (gcloud auth login / ADC)
  - The caller's account must have roles/cloudasset.viewer on the project.

Supported resource types (same as the CSV-based loader):
  compute.Network, compute.Subnetwork, compute.Firewall,
  compute.Router, compute.Address, compute.InterconnectAttachment,
  compute.NetworkEndpointGroup, dns.ManagedZone, dns.ResourceRecordSet
"""

import json
import re
import subprocess
from typing import Any

from agent.config import GEMINI_API_KEY, GEMINI_MODEL

ASSET_TYPES = [
    "compute.googleapis.com/Network",
    "compute.googleapis.com/Subnetwork",
    "compute.googleapis.com/Firewall",
    "compute.googleapis.com/Router",
    "compute.googleapis.com/Address",
    "compute.googleapis.com/GlobalAddress",
    "compute.googleapis.com/InterconnectAttachment",
    "compute.googleapis.com/NetworkEndpointGroup",
    "servicenetworking.googleapis.com/Connection",
    "dns.googleapis.com/ManagedZone",
    "dns.googleapis.com/ResourceRecordSet",
]

SUPPORTED_ASSET_TYPES = ASSET_TYPES + [
    "compute.googleapis.com/Project",
]

_ALIASES = {
    "compute.network": "compute.googleapis.com/Network",
    "network": "compute.googleapis.com/Network",
    "vpc": "compute.googleapis.com/Network",
    "vpcs": "compute.googleapis.com/Network",
    "compute.subnetwork": "compute.googleapis.com/Subnetwork",
    "subnetwork": "compute.googleapis.com/Subnetwork",
    "subnet": "compute.googleapis.com/Subnetwork",
    "subnets": "compute.googleapis.com/Subnetwork",
    "compute.firewall": "compute.googleapis.com/Firewall",
    "firewall": "compute.googleapis.com/Firewall",
    "firewalls": "compute.googleapis.com/Firewall",
    "compute.router": "compute.googleapis.com/Router",
    "router": "compute.googleapis.com/Router",
    "routers": "compute.googleapis.com/Router",
    "compute.routernat": "compute.googleapis.com/Router",
    "routernat": "compute.googleapis.com/Router",
    "router nat": "compute.googleapis.com/Router",
    "router_nat": "compute.googleapis.com/Router",
    "nat": "compute.googleapis.com/Router",
    "compute.routerinterface": "compute.googleapis.com/Router",
    "routerinterface": "compute.googleapis.com/Router",
    "router interface": "compute.googleapis.com/Router",
    "router_interface": "compute.googleapis.com/Router",
    "compute.routerpeer": "compute.googleapis.com/Router",
    "routerpeer": "compute.googleapis.com/Router",
    "router peer": "compute.googleapis.com/Router",
    "router_peer": "compute.googleapis.com/Router",
    "compute.address": "compute.googleapis.com/Address",
    "compute.globaladdress": "compute.googleapis.com/GlobalAddress",
    "globaladdress": "compute.googleapis.com/GlobalAddress",
    "global address": "compute.googleapis.com/GlobalAddress",
    "global_address": "compute.googleapis.com/GlobalAddress",
    "compute.interconnectattachment": "compute.googleapis.com/InterconnectAttachment",
    "interconnectattachment": "compute.googleapis.com/InterconnectAttachment",
    "interconnect attachment": "compute.googleapis.com/InterconnectAttachment",
    "interconnect_attachment": "compute.googleapis.com/InterconnectAttachment",
    "vlanattachment": "compute.googleapis.com/InterconnectAttachment",
    "vlan attachment": "compute.googleapis.com/InterconnectAttachment",
    "vlan_attachment": "compute.googleapis.com/InterconnectAttachment",
    "compute.networkendpointgroup": "compute.googleapis.com/NetworkEndpointGroup",
    "networkendpointgroup": "compute.googleapis.com/NetworkEndpointGroup",
    "network endpoint group": "compute.googleapis.com/NetworkEndpointGroup",
    "neg": "compute.googleapis.com/NetworkEndpointGroup",
    "negs": "compute.googleapis.com/NetworkEndpointGroup",
    "servicenetworking.connection": "servicenetworking.googleapis.com/Connection",
    "servicenetworkingconnection": "servicenetworking.googleapis.com/Connection",
    "service networking connection": "servicenetworking.googleapis.com/Connection",
    "service_networking_connection": "servicenetworking.googleapis.com/Connection",
    "compute.sharedvpchostproject": "compute.googleapis.com/Project",
    "sharedvpchostproject": "compute.googleapis.com/Project",
    "shared vpc host project": "compute.googleapis.com/Project",
    "shared_vpc_host_project": "compute.googleapis.com/Project",
    "computesharedvpcserviceproject": "compute.googleapis.com/Project",
    "compute.sharedvpcserviceproject": "compute.googleapis.com/Project",
    "sharedvpcserviceproject": "compute.googleapis.com/Project",
    "shared vpc service project": "compute.googleapis.com/Project",
    "shared_vpc_service_project": "compute.googleapis.com/Project",
    "iampartialpolicy": "compute.googleapis.com/Project",
    "iam.partialpolicy": "compute.googleapis.com/Project",
    "iam partial policy": "compute.googleapis.com/Project",
    "iam_partial_policy": "compute.googleapis.com/Project",
    "dns.managedzone": "dns.googleapis.com/ManagedZone",
    "dnsmanagedzone": "dns.googleapis.com/ManagedZone",
    "managedzone": "dns.googleapis.com/ManagedZone",
    "managed zone": "dns.googleapis.com/ManagedZone",
    "dns zone": "dns.googleapis.com/ManagedZone",
    "dns": "dns.googleapis.com/ManagedZone",
    "dns.recordset": "dns.googleapis.com/ResourceRecordSet",
    "dnsresourcerecordset": "dns.googleapis.com/ResourceRecordSet",
    "dnsrecordset": "dns.googleapis.com/ResourceRecordSet",
    "recordset": "dns.googleapis.com/ResourceRecordSet",
    "record set": "dns.googleapis.com/ResourceRecordSet",
}

_GROUP_ALIASES = {
    "vpc": [
        "compute.googleapis.com/Network",
        "compute.googleapis.com/Subnetwork",
        "compute.googleapis.com/Address",
        "compute.googleapis.com/GlobalAddress",
        "compute.googleapis.com/Router",
        "compute.googleapis.com/InterconnectAttachment",
        "servicenetworking.googleapis.com/Connection",
    ],
    "networking": [
        "compute.googleapis.com/Network",
        "compute.googleapis.com/Subnetwork",
        "compute.googleapis.com/Address",
        "compute.googleapis.com/GlobalAddress",
        "compute.googleapis.com/Router",
        "compute.googleapis.com/InterconnectAttachment",
        "servicenetworking.googleapis.com/Connection",
    ],
    "dns": [
        "dns.googleapis.com/ManagedZone",
        "dns.googleapis.com/ResourceRecordSet",
    ],
    "address": [
        "compute.googleapis.com/Address",
        "compute.googleapis.com/GlobalAddress",
    ],
    "addresses": [
        "compute.googleapis.com/Address",
        "compute.googleapis.com/GlobalAddress",
    ],
    "ip": [
        "compute.googleapis.com/Address",
        "compute.googleapis.com/GlobalAddress",
    ],
    "ips": [
        "compute.googleapis.com/Address",
        "compute.googleapis.com/GlobalAddress",
    ],
}

# Short type name → canonical loader key
_TYPE_MAP = {
    "Network":               "compute.Network",
    "Subnetwork":            "compute.Subnetwork",
    "Firewall":              "compute.Firewall",
    "Router":                "compute.Router",
    "Address":               "compute.Address",
    "GlobalAddress":         "compute.Address",
    "Project":               "compute.Project",
    "InterconnectAttachment": "compute.InterconnectAttachment",
    "NetworkEndpointGroup":  "compute.NetworkEndpointGroup",
    "Connection":            "serviceNetworking.Connection",
    "ManagedZone":           "dns.ManagedZone",
    "ResourceRecordSet":     "dns.ResourceRecordSet",
}


def fetch(
    project_id: str,
    resource_types: list[str] | None = None,
    access_query: str | None = None,
    merge_existing: bool = True,
) -> list[dict]:
    """
    Call Cloud Asset Inventory for `project_id` and return a list of
    normalised rows that look exactly like what loader.py expects.

    The raw JSON and the normalised CSV are both saved to:
      inventory_cache/<project_id>_raw.json
      inventory_cache/<project_id>_inventory.csv
    """
    import csv
    from pathlib import Path

    requested = _split_resource_types(resource_types or [])
    query_plan = _build_query_plan(requested, access_query)

    print(f"  🔍  Querying GCP assets for project: {project_id} ...")
    print(f"  Asset types: {', '.join(query_plan['asset_types'])}")
    print(f"  SQL statements: {len(query_plan['statements'])}")
    raw = _call_gcloud(project_id, query_plan)

    # ── Save raw JSON ───────────────────────────────────────────────────
    cache_dir = Path(__file__).parent.parent.parent / "inventory_cache"
    cache_dir.mkdir(exist_ok=True)
    raw_path = cache_dir / f"{project_id}_raw.json"
    raw_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    print(f"  💾  Raw asset query JSON saved → {raw_path}")

    query_path = cache_dir / f"{project_id}_access_query.json"
    query_path.write_text(json.dumps(query_plan, indent=2), encoding="utf-8")
    print(f"  Access query saved -> {query_path}")

    rows = []
    for result_row in raw:
        row = _query_row_to_loader_row(result_row, project_id)
        if row:
            rows.append(row)

    # ── Save normalised CSV ─────────────────────────────────────────────
    csv_path = cache_dir / f"{project_id}_inventory.csv"
    fieldnames = ["Name", "Resource type", "Project Id", "Location", "Additional attributes"]
    output_rows = _merge_inventory_rows(csv_path, rows) if merge_existing else rows
    if output_rows:
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(output_rows)
        print(f"  💾  Normalised inventory CSV saved → {csv_path}")

    print(f"  📋  Retrieved {len(rows)} Terraform-supported resources from Cloud Asset API.")
    if merge_existing and len(output_rows) != len(rows):
        print(f"  Merged {len(rows)} fetched rows into {len(output_rows)} cached inventory rows.")
    return output_rows


# ── Access query generation ────────────────────────────────────────────────

def _split_resource_types(values: list[str]) -> list[str]:
    items: list[str] = []
    for value in values:
        items.extend(part.strip() for part in str(value).split(","))
    return [item for item in items if item]


def _merge_inventory_rows(csv_path, new_rows: list[dict]) -> list[dict]:
    existing_rows = _read_inventory_csv(csv_path)
    merged = {_row_key(row): row for row in existing_rows if _row_key(row)}
    for row in new_rows:
        key = _row_key(row)
        if key:
            merged[key] = row
    return list(merged.values())


def _read_inventory_csv(csv_path) -> list[dict]:
    import csv

    if not csv_path.exists():
        return []
    with open(csv_path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _row_key(row: dict) -> tuple:
    return (
        str(row.get("Project Id", "")).strip(),
        str(row.get("Resource type", "")).strip(),
        str(row.get("Location", "")).strip(),
        str(row.get("Name", "")).strip(),
    )


def _build_query_plan(resource_types: list[str], access_query: str | None) -> dict:
    manual_sql = (access_query or "").strip()
    if manual_sql:
        return {
            "resource_types": resource_types,
            "asset_types": _resolve_asset_types(resource_types) if resource_types else ["manual-sql"],
            "statements": [{
                "asset_type": "manual-sql",
                "resource_type": "",
                "statement": manual_sql,
            }],
            "source": "manual",
        }

    selected_types = _resolve_asset_types(resource_types) if resource_types else ASSET_TYPES

    if resource_types and not _all_resource_types_known(resource_types):
        gemini_plan = _generate_query_with_gemini(resource_types, selected_types, access_query)
        if gemini_plan:
            return gemini_plan

    statements = [
        {
            "asset_type": asset_type,
            "resource_type": _canonical_for_asset_type(asset_type),
            "statement": (access_query or "").strip() or _fallback_statement(asset_type),
        }
        for asset_type in selected_types
    ]
    source = "manual" if access_query else "deterministic-fallback"
    if not resource_types:
        source = "default"
    return {
        "resource_types": resource_types,
        "asset_types": selected_types,
        "statements": statements,
        "source": source,
    }


def _generate_query_with_gemini(
    resource_types: list[str],
    selected_asset_types: list[str],
    access_query: str | None,
) -> dict | None:
    if access_query:
        return None

    if not GEMINI_API_KEY:
        print("  GEMINI_API_KEY is not set; using deterministic asset SQL templates.")
        return None

    try:
        from google import genai
        from google.genai import types

        supported_tables = {
            asset_type: _table_for_asset_type(asset_type)
            for asset_type in selected_asset_types
        }
        examples = {
            asset_type: _fallback_statement(asset_type)
            for asset_type in selected_asset_types
        }

        client = genai.Client(api_key=GEMINI_API_KEY)
        config = types.GenerateContentConfig(
            system_instruction=(
                "You generate Cloud Asset Inventory queryAssets SQL for "
                "Terraform inventory extraction. Return only compact JSON."
            ),
        )
        prompt = f"""
Create Cloud Asset Inventory SQL statements for the user requested resource types.

User resource_type values:
{json.dumps(resource_types)}

Supported asset type to table mapping:
{json.dumps(supported_tables, indent=2)}

Required output columns for every SELECT:
- Name
- Resource_type
- Project_Id
- Location
- Additional_attributes

Additional_attributes must be a JSON string. Prefer TO_JSON_STRING(resource.data)
so downstream Terraform generation can extract resource-specific fields.

Examples to follow:
{json.dumps(examples, indent=2)}

Return JSON with exactly this shape:
{{
  "statements": [
    {{
      "asset_type": "compute.googleapis.com/Network",
      "resource_type": "compute.Network",
      "statement": "SELECT ..."
    }}
  ]
}}

Rules:
- Generate one statement per selected table.
- Use only the provided table names.
- Do not add markdown or explanations.
- Do not use DML, DDL, EXPORT, INSERT, UPDATE, DELETE, or scripting.
"""
        raw = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=config,
        ).text.strip()
        plan = json.loads(_strip_fences(raw))
        statements = _validate_statements(plan.get("statements", []), selected_asset_types)
        if not statements:
            return None
        return {
            "resource_types": resource_types,
            "asset_types": selected_asset_types,
            "statements": statements,
            "source": f"gemini:{GEMINI_MODEL}",
        }
    except Exception as exc:
        print(f"  Gemini asset SQL generation failed: {exc}")
        print("      Falling back to deterministic asset SQL templates.")
        return None


def _validate_statements(raw_statements: list, selected_asset_types: list[str]) -> list[dict]:
    allowed_tables = {_table_for_asset_type(asset_type) for asset_type in selected_asset_types}
    statements: list[dict] = []
    for item in raw_statements:
        if not isinstance(item, dict):
            continue
        asset_type = item.get("asset_type")
        statement = str(item.get("statement") or "").strip()
        if asset_type not in selected_asset_types or not _is_safe_select(statement, allowed_tables):
            continue
        for required in ("Name", "Resource_type", "Project_Id", "Location", "Additional_attributes"):
            if required.lower() not in statement.lower():
                statement = _fallback_statement(asset_type)
                break
        statements.append({
            "asset_type": asset_type,
            "resource_type": _canonical_for_asset_type(asset_type),
            "statement": statement,
        })
    return statements


def _is_safe_select(statement: str, allowed_tables: set[str]) -> bool:
    lowered = statement.lower()
    banned = (" insert ", " update ", " delete ", " create ", " drop ", " export ", " merge ")
    if not lowered.startswith("select") or any(token in lowered for token in banned):
        return False
    return any(f"from {table.lower()}" in lowered for table in allowed_tables)


def _fallback_statement(asset_type: str) -> str:
    table = _table_for_asset_type(asset_type)
    canonical = _canonical_for_asset_type(asset_type)
    location_expr = _location_expression(canonical)
    if canonical == "dns.ResourceRecordSet":
        return f"""SELECT
  CONCAT(resource.data.name, "-", resource.data.type) AS Name,
  "{canonical}" AS Resource_type,
  REGEXP_EXTRACT(name, r"projects/([^/]+)") AS Project_Id,
  {location_expr} AS Location,
  TO_JSON_STRING(STRUCT(
    resource.data.name AS name,
    resource.data.type AS type,
    resource.data.ttl AS ttl,
    resource.data.rrdata AS rrdata,
    REGEXP_EXTRACT(name, r"/managedZones/([^/]+)") AS managedZone
  )) AS Additional_attributes
FROM
  {table}
ORDER BY
  resource.data.name"""
    if canonical == "serviceNetworking.Connection":
        return f"""SELECT
  COALESCE(
    resource.data.peering,
    REGEXP_EXTRACT(resource.data.network, r"/networks/([^/]+)"),
    REGEXP_EXTRACT(name, r"/connections/([^/]+)"),
    "service-networking-connection"
  ) AS Name,
  "{canonical}" AS Resource_type,
  REGEXP_EXTRACT(name, r"projects/([^/]+)") AS Project_Id,
  {location_expr} AS Location,
  TO_JSON_STRING(resource.data) AS Additional_attributes
FROM
  {table}
ORDER BY
  Name"""
    return f"""SELECT
  resource.data.name AS Name,
  "{canonical}" AS Resource_type,
  REGEXP_EXTRACT(name, r"projects/([^/]+)") AS Project_Id,
  {location_expr} AS Location,
  TO_JSON_STRING(resource.data) AS Additional_attributes
FROM
  {table}
ORDER BY
  resource.data.name"""


def _table_for_asset_type(asset_type: str) -> str:
    return asset_type.replace(".", "_").replace("/", "_")


def _canonical_for_asset_type(asset_type: str) -> str:
    return _TYPE_MAP.get(asset_type.split("/")[-1], "")


def _location_expression(canonical: str) -> str:
    if canonical in {"compute.Subnetwork", "compute.Router", "compute.InterconnectAttachment"}:
        return 'REGEXP_EXTRACT(name, r"/regions/([^/]+)")'
    if canonical == "compute.NetworkEndpointGroup":
        return 'COALESCE(REGEXP_EXTRACT(name, r"/zones/([^/]+)"), REGEXP_EXTRACT(name, r"/regions/([^/]+)"), "global")'
    if canonical == "compute.Address":
        return 'COALESCE(REGEXP_EXTRACT(name, r"/regions/([^/]+)"), "global")'
    if canonical in {"dns.ManagedZone", "dns.ResourceRecordSet"}:
        return 'COALESCE(REGEXP_EXTRACT(name, r"/locations/([^/]+)"), "global")'
    if canonical == "serviceNetworking.Connection":
        return '"global"'
    return '"global"'


def _resolve_asset_types(resource_types: list[str]) -> list[str]:
    resolved: list[str] = []
    for item in resource_types:
        key = item.strip().lower()
        grouped = _GROUP_ALIASES.get(_alias_key(key))
        if grouped:
            for asset_type in grouped:
                if asset_type in SUPPORTED_ASSET_TYPES and asset_type not in resolved:
                    resolved.append(asset_type)
            continue
        asset_type = _lookup_alias(key)
        if not asset_type and item.startswith("compute.googleapis.com/"):
            asset_type = item
        if not asset_type and item.startswith("dns.googleapis.com/"):
            asset_type = item
        if not asset_type and item.startswith("servicenetworking.googleapis.com/"):
            asset_type = item
        if not asset_type and item.startswith("compute."):
            suffix = item.split(".", 1)[1]
            asset_type = _lookup_alias(suffix.lower())
        if asset_type and asset_type in SUPPORTED_ASSET_TYPES and asset_type not in resolved:
            resolved.append(asset_type)
    return resolved or ASSET_TYPES


def _all_resource_types_known(resource_types: list[str]) -> bool:
    return all(_is_known_resource_type(item) for item in resource_types)


def _is_known_resource_type(item: str) -> bool:
    key = item.strip().lower()
    if _GROUP_ALIASES.get(_alias_key(key)):
        return True
    if _lookup_alias(key):
        return True
    if item.startswith(("compute.googleapis.com/", "dns.googleapis.com/", "servicenetworking.googleapis.com/")):
        return item in SUPPORTED_ASSET_TYPES
    if item.startswith("compute."):
        suffix = item.split(".", 1)[1]
        return bool(_lookup_alias(suffix.lower()))
    return False


def _lookup_alias(value: str) -> str:
    return (
        _ALIASES.get(value)
        or _ALIASES.get(_alias_key(value))
        or _ALIASES.get(value.replace("-", ""))
    )


def _alias_key(value: str) -> str:
    return re.sub(r"[\s_-]+", " ", value.strip().lower())


def _strip_fences(value: str) -> str:
    value = re.sub(r"^```[a-zA-Z]*\n?", "", value.strip())
    value = re.sub(r"\n?```$", "", value.strip())
    return value


# ── gcloud call ────────────────────────────────────────────────────────────

def _call_gcloud(project_id: str, query_plan: dict) -> list[dict]:
    rows: list[dict] = []
    for query in query_plan["statements"]:
        rows.extend(_run_asset_query(project_id, query["statement"]))
    return rows


def _run_asset_query(project_id: str, statement: str) -> list[dict]:
    cmd = [
        "gcloud", "asset", "query",
        f"--project={project_id}",
        f"--statement={statement}",
        "--format=json",
    ]
    response = _run_gcloud_json(cmd, project_id)
    rows = _query_response_rows(response)

    job_ref = response.get("jobReference") or response.get("job_reference")
    next_token = _next_page_token(response)
    while job_ref and next_token:
        page_cmd = [
            "gcloud", "asset", "query",
            f"--project={project_id}",
            f"--job-reference={job_ref}",
            f"--page-token={next_token}",
            "--format=json",
        ]
        response = _run_gcloud_json(page_cmd, project_id)
        rows.extend(_query_response_rows(response))
        next_token = _next_page_token(response)

    return rows


def _run_gcloud_json(cmd: list[str], project_id: str) -> dict:
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=True
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip()
        permission_hint = _permission_hint(stderr, project_id)
        raise RuntimeError(
            f"gcloud asset query failed:\n{stderr}\n\n"
            "Make sure:\n"
            "  1. gcloud CLI is installed and on PATH.\n"
            "  2. You are authenticated: gcloud auth application-default login\n"
            "  3. Cloud Asset API is enabled: "
            f"gcloud services enable cloudasset.googleapis.com --project={project_id}\n"
            "  4. Your account has cloudasset.assets.queryAssets permission "
            f"on project {project_id}."
            f"{permission_hint}"
        ) from exc
    except FileNotFoundError:
        raise RuntimeError(
            "gcloud CLI not found. Install Google Cloud SDK:\n"
            "  https://cloud.google.com/sdk/docs/install"
        )

    try:
        response = json.loads(result.stdout) or {}
    except json.JSONDecodeError:
        raise RuntimeError(
            f"Failed to parse gcloud output as JSON:\n{result.stdout[:500]}"
        )
    return response


def _permission_hint(stderr: str, project_id: str) -> str:
    if "USER_PROJECT_DENIED" not in stderr and "serviceusage.services.use" not in stderr:
        return ""
    return (
        "\n\nDetected USER_PROJECT_DENIED / serviceusage.services.use failure.\n"
        "Ask a project IAM admin to grant this active gcloud account:\n"
        f"  roles/serviceusage.serviceUsageConsumer on project {project_id}\n"
        "You also need a role that includes cloudasset.assets.queryAssets, "
        "for example roles/cloudasset.viewer.\n\n"
        "Example admin commands:\n"
        f"  gcloud projects add-iam-policy-binding {project_id} "
        "--member=\"user:YOUR_EMAIL\" --role=\"roles/serviceusage.serviceUsageConsumer\"\n"
        f"  gcloud projects add-iam-policy-binding {project_id} "
        "--member=\"user:YOUR_EMAIL\" --role=\"roles/cloudasset.viewer\""
    )


def _next_page_token(response: dict) -> str:
    query_result = response.get("queryResult") or response.get("query_result") or {}
    return str(query_result.get("nextPageToken") or query_result.get("next_page_token") or "")


def _query_response_rows(response: dict) -> list[dict]:
    query_result = response.get("queryResult") or response.get("query_result") or response
    schema = query_result.get("schema") or {}
    fields = schema.get("fields") or []
    names = [field.get("field") or field.get("name") for field in fields]
    rows = query_result.get("rows") or []

    parsed: list[dict] = []
    for row in rows:
        if isinstance(row, dict) and "f" in row:
            cells = row.get("f") or []
            parsed.append({
                names[idx]: _decode_query_value(cell.get("v"))
                for idx, cell in enumerate(cells)
                if idx < len(names) and names[idx]
            })
        elif isinstance(row, dict):
            parsed.append({
                key: _decode_query_value(value)
                for key, value in row.items()
            })
    return parsed


def _decode_query_value(value):
    if isinstance(value, dict) and "v" in value:
        return _decode_query_value(value["v"])
    return value


def _query_row_to_loader_row(row: dict, project_id: str) -> dict | None:
    name = row.get("Name") or row.get("name") or row.get("vpc_name")
    resource_type = row.get("Resource_type") or row.get("Resource type")
    if not name or not resource_type:
        return None
    attrs = row.get("Additional_attributes") or row.get("Additional attributes") or ""
    if isinstance(attrs, dict):
        attrs = json.dumps(attrs)
    return {
        "Name": str(name),
        "Resource type": str(resource_type),
        "Project Id": str(row.get("Project_Id") or row.get("Project Id") or project_id),
        "Location": str(row.get("Location") or row.get("location") or "global"),
        "Additional attributes": str(attrs or ""),
    }


# ── Asset → row ────────────────────────────────────────────────────────────

def _asset_to_row(asset: dict, project_id: str) -> dict | None:
    """Convert a Cloud Asset Inventory entry to a loader-compatible row."""
    asset_type = asset.get("assetType", "")          # e.g. compute.googleapis.com/Network
    data       = _resource_data(asset)
    name_full  = asset.get("name", "")               # //compute.googleapis.com/projects/.../networks/my-vpc

    # Derive short type name
    short_type = asset_type.split("/")[-1]            # e.g. "Network"
    canonical  = _TYPE_MAP.get(short_type)
    if not canonical:
        return None

    # Resource name (last path segment)
    res_name = data.get("name") or asset.get("displayName") or name_full.split("/")[-1]

    # Location — extract from selfLink or name path
    location = _extract_location(data, name_full, canonical, asset.get("location", ""))

    # Additional attributes (gateway for subnets, address for IPs, etc.)
    extra = _extract_attrs(data, canonical)

    return {
        "Name":                  res_name,
        "Resource type":         canonical,
        "Project Id":            data.get("projectId") or _project_from_asset(asset) or project_id,
        "Location":              location,
        "Additional attributes": extra,
    }


def _resource_data(asset: dict) -> dict:
    resource = asset.get("resource", {})
    if isinstance(resource, dict) and isinstance(resource.get("data"), dict):
        return resource["data"]

    versioned = asset.get("versionedResources") or asset.get("versioned_resources") or []
    if isinstance(versioned, list) and versioned:
        first = versioned[0] if isinstance(versioned[0], dict) else {}
        data = first.get("resource") or first.get("data") or {}
        if isinstance(data, dict):
            return data

    attrs = asset.get("additionalAttributes") or asset.get("additional_attributes") or {}
    return attrs if isinstance(attrs, dict) else {}


def _project_from_asset(asset: dict) -> str:
    project = str(asset.get("project") or "")
    return project.split("/")[-1] if project else ""


def _extract_location(data: dict, name_full: str, rtype: str, asset_location: str = "") -> str:
    """Infer region/zone/global from resource data or asset name."""
    if asset_location and asset_location.lower() != "global":
        return asset_location

    # Addresses and Routers have a 'region' field
    region = data.get("region", "")
    if region:
        return region.split("/")[-1]

    # Subnets
    if rtype == "compute.Subnetwork":
        region = data.get("region", "")
        if region:
            return region.split("/")[-1]

    # NEGs have a 'zone' field
    zone = data.get("zone", "")
    if zone:
        return zone.split("/")[-1]

    # Parse from the asset name path: .../regions/us-central1/...
    m = re.search(r"/regions/([^/]+)", name_full)
    if m:
        return m.group(1)
    m = re.search(r"/zones/([^/]+)", name_full)
    if m:
        return m.group(1)

    return "global"


def _extract_attrs(data: dict, rtype: str) -> str:
    """Return a JSON string of relevant extra attributes for each resource type."""
    attrs: dict[str, Any] = {}
    if rtype == "compute.Network":
        attrs.update(data)
    elif rtype == "compute.Subnetwork":
        for field in (
            "gatewayAddress", "ipCidrRange", "network", "description",
            "privateIpGoogleAccess", "secondaryIpRanges", "stackType",
            "logConfig", "enableFlowLogs", "purpose", "role",
        ):
            val = data.get(field)
            if val is not None:
                attrs[field] = val
    elif rtype == "compute.Address":
        for field in ("address", "description", "addressType", "networkTier"):
            val = data.get(field)
            if val is not None:
                attrs[field] = val
    elif rtype == "compute.Firewall":
        attrs.update(data)
    elif rtype in {
        "compute.Router",
        "compute.Project",
        "compute.InterconnectAttachment",
        "compute.NetworkEndpointGroup",
        "serviceNetworking.Connection",
        "dns.ManagedZone",
        "dns.ResourceRecordSet",
    }:
        attrs.update(data)
        val = data.get("network")
        if val is not None:
            attrs["network"] = val
    return json.dumps(attrs) if attrs else ""
