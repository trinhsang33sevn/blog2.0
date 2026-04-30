"""Hashnode GraphQL API integration (Personal Access Token)."""
import httpx

HASHNODE_GQL = "https://gql.hashnode.com"


def _gql(api_key: str, query: str, variables: dict | None = None) -> dict:
    with httpx.Client(timeout=30) as c:
        resp = c.post(
            HASHNODE_GQL,
            headers={"Authorization": api_key, "Content-Type": "application/json"},
            json={"query": query, "variables": variables or {}},
        )
        resp.raise_for_status()
        data = resp.json()
    if "errors" in data:
        raise ValueError(str(data["errors"]))
    return data["data"]


def get_user_publications(api_key: str) -> list[dict]:
    query = """
    query {
      me {
        publications(first: 20) {
          edges {
            node { id title url }
          }
        }
      }
    }
    """
    data  = _gql(api_key, query)
    edges = data.get("me", {}).get("publications", {}).get("edges", [])
    return [
        {"id": e["node"]["id"], "name": e["node"]["title"], "url": e["node"]["url"]}
        for e in edges
    ]


def publish_post(api_key: str, publication_id: str, title: str, content: str,
                 tags: list | None = None) -> dict:
    mutation = """
    mutation PublishPost($input: PublishPostInput!) {
      publishPost(input: $input) {
        post { id url }
      }
    }
    """
    tag_inputs = [
        {"name": t, "slug": t.lower().replace(" ", "-")}
        for t in (tags or [])
    ][:5]
    variables = {
        "input": {
            "title":           title,
            "contentMarkdown": content,
            "publicationId":   publication_id,
            "tags":            tag_inputs,
        }
    }
    data = _gql(api_key, mutation, variables)
    post = data.get("publishPost", {}).get("post", {})
    return {"url": post.get("url", ""), "id": post.get("id", "")}


def update_post(api_key: str, post_id: str, title: str, content: str) -> dict:
    mutation = """
    mutation UpdatePost($input: UpdatePostInput!) {
      updatePost(input: $input) {
        post { id url }
      }
    }
    """
    data = _gql(api_key, mutation, {"input": {
        "id":              post_id,
        "title":           title,
        "contentMarkdown": content,
    }})
    post = data.get("updatePost", {}).get("post", {})
    return {"url": post.get("url", ""), "id": post.get("id", post_id)}
