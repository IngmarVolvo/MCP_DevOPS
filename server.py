import base64
import os
from typing import Any
from urllib.parse import quote

import httpx
from mcp.server.fastmcp import FastMCP

ORG = os.getenv("AZDO_ORG", "VolvoGroup-UDP")
PROJECT = os.getenv("AZDO_PROJECT", "SMLCloud")
PAT = os.getenv("AZDO_PAT", "")
HOST = os.getenv("MCP_HOST", "0.0.0.0")
PORT = int(os.getenv("MCP_PORT", "8000"))

if not PAT:
    raise RuntimeError("AZDO_PAT is required. Use a read-only Azure DevOps PAT and keep it server-side.")

mcp = FastMCP(
    "SMLCloud DevOps Reader",
    stateless_http=True,
    json_response=True,
)


def _headers() -> dict[str, str]:
    token = base64.b64encode(f":{PAT}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}


async def _request(method: str, url: str, **kwargs: Any) -> Any:
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        response = await client.request(method, url, headers=_headers(), **kwargs)
        if response.status_code >= 400:
            detail = response.text[:1000]
            raise RuntimeError(f"Azure DevOps returned {response.status_code}: {detail}")
        return response.json()


def _work_item_summary(item: dict[str, Any]) -> dict[str, Any]:
    fields = item.get("fields", {})
    return {
        "id": item.get("id"),
        "type": fields.get("System.WorkItemType"),
        "title": fields.get("System.Title"),
        "state": fields.get("System.State"),
        "assigned_to": (fields.get("System.AssignedTo") or {}).get("displayName")
            if isinstance(fields.get("System.AssignedTo"), dict)
            else fields.get("System.AssignedTo"),
        "area_path": fields.get("System.AreaPath"),
        "iteration_path": fields.get("System.IterationPath"),
        "url": item.get("_links", {}).get("html", {}).get("href"),
    }


@mcp.tool(description="Health check showing the configured Azure DevOps organization and project.")
async def health() -> dict[str, str]:
    return {"status": "ok", "organization": ORG, "project": PROJECT, "mode": "read-only"}


@mcp.tool(description="Get one SMLCloud Azure DevOps work item by numeric ID. Read-only.")
async def get_work_item(work_item_id: int) -> dict[str, Any]:
    url = f"https://dev.azure.com/{quote(ORG)}/{quote(PROJECT)}/_apis/wit/workitems/{work_item_id}?api-version=7.1&$expand=relations"
    item = await _request("GET", url)
    result = _work_item_summary(item)
    fields = item.get("fields", {})
    result.update({
        "description": fields.get("System.Description"),
        "acceptance_criteria": fields.get("Microsoft.VSTS.Common.AcceptanceCriteria"),
        "tags": fields.get("System.Tags"),
        "relations": item.get("relations", []),
    })
    return result


@mcp.tool(description="Search SMLCloud work items by words in title or description. Returns at most 25 read-only results.")
async def search_work_items(search_text: str, max_results: int = 25) -> list[dict[str, Any]]:
    text = search_text.strip().replace("'", "''")
    if not text:
        raise ValueError("search_text must not be empty")
    top = max(1, min(max_results, 25))
    wiql = {
        "query": (
            "SELECT [System.Id] FROM WorkItems "
            f"WHERE [System.TeamProject] = '{PROJECT.replace(chr(39), chr(39)*2)}' "
            "AND [System.WorkItemType] <> '' "
            f"AND ([System.Title] CONTAINS '{text}' OR [System.Description] CONTAINS '{text}') "
            "ORDER BY [System.ChangedDate] DESC"
        )
    }
    wiql_url = f"https://dev.azure.com/{quote(ORG)}/{quote(PROJECT)}/_apis/wit/wiql?api-version=7.1&$top={top}"
    found = await _request("POST", wiql_url, json=wiql)
    ids = [str(x["id"]) for x in found.get("workItems", [])[:top]]
    if not ids:
        return []
    fields = ",".join([
        "System.Id", "System.WorkItemType", "System.Title", "System.State",
        "System.AssignedTo", "System.AreaPath", "System.IterationPath"
    ])
    batch_url = f"https://dev.azure.com/{quote(ORG)}/{quote(PROJECT)}/_apis/wit/workitems?ids={','.join(ids)}&fields={quote(fields)}&api-version=7.1"
    data = await _request("GET", batch_url)
    return [_work_item_summary(item) for item in data.get("value", [])]


@mcp.tool(description="List direct child work items of a parent Azure DevOps work item. Read-only.")
async def get_work_item_children(parent_id: int) -> list[dict[str, Any]]:
    parent = await get_work_item(parent_id)
    child_ids = []
    for relation in parent.get("relations", []):
        if relation.get("rel") == "System.LinkTypes.Hierarchy-Forward":
            try:
                child_ids.append(relation["url"].rstrip("/").split("/")[-1])
            except (KeyError, IndexError):
                pass
    if not child_ids:
        return []
    fields = ",".join(["System.Id", "System.WorkItemType", "System.Title", "System.State", "System.AssignedTo"])
    url = f"https://dev.azure.com/{quote(ORG)}/{quote(PROJECT)}/_apis/wit/workitems?ids={','.join(child_ids[:50])}&fields={quote(fields)}&api-version=7.1"
    data = await _request("GET", url)
    return [_work_item_summary(item) for item in data.get("value", [])]


@mcp.tool(description="List repositories in the SMLCloud Azure DevOps project. Read-only.")
async def list_repositories() -> list[dict[str, Any]]:
    url = f"https://dev.azure.com/{quote(ORG)}/{quote(PROJECT)}/_apis/git/repositories?api-version=7.1"
    data = await _request("GET", url)
    return [{"id": r.get("id"), "name": r.get("name"), "default_branch": r.get("defaultBranch"), "web_url": r.get("webUrl")} for r in data.get("value", [])]


@mcp.tool(description="Read a UTF-8 text file from an SMLCloud Azure DevOps repository. Read-only; max 200 KB.")
async def get_repository_file(repository: str, path: str, version: str = "main") -> dict[str, Any]:
    safe_path = path if path.startswith("/") else "/" + path
    url = (
        f"https://dev.azure.com/{quote(ORG)}/{quote(PROJECT)}/_apis/git/repositories/{quote(repository)}/items"
        f"?path={quote(safe_path)}&versionDescriptor.version={quote(version)}&includeContent=true&api-version=7.1"
    )
    data = await _request("GET", url)
    content = data.get("content", "")
    if len(content.encode("utf-8")) > 200_000:
        raise ValueError("File exceeds the 200 KB read limit")
    return {"repository": repository, "path": safe_path, "version": version, "content": content}


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host=HOST, port=PORT)
