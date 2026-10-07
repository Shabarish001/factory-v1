# Bounded research workflow

`ResearchWorkflow` combines provider-neutral search and safe public page fetching into a deterministic source bundle:

```text
validated plan -> searches -> URL deduplication/ranking -> bounded fetches -> source bundle
```

It does not call an LLM, synthesize findings, crawl links, or modify a target repository.

## Programmatic interface

```python
from tools.research import ResearchPlan, ResearchWorkflow
from tools.search import create_default_search_service
from tools.web import PublicPageFetcher

plan = ResearchPlan(
    objective="Compare official booking capabilities",
    queries=["official product booking features", "official booking API"],
    allowed_domains=["example.com"],
    max_pages=4,
)

workflow = ResearchWorkflow(create_default_search_service(), PublicPageFetcher())
bundle = workflow.run(plan)
```

Pass an active `RunArtifactRecorder` to the workflow to persist the plan, search/fetch events, and source bundle.

## CLI

```text
python -m tools.research.run research-plan.json --target ../FindAndBook
```

The CLI requires `TAVILY_API_KEY`, prints the normalized bundle as JSON, and stores artifacts under Factory. It does not write to the target.

## Deterministic selection

URLs are normalized, common tracking parameters are removed, and duplicates are merged. Domain allow/block policy is enforced both in search requests and after results return. Candidates are sorted by descending provider score, then query order, result order, and canonical URL. IDs `S1`, `S2`, and so on are assigned before fetching, so a failed page retains its ID and cannot shift later citations.

Search snippets are not copied into the evidence bundle. Only successfully fetched, bounded page text can support source citations.
