"""Write the live tool definitions (input/output JSON Schemas) to docs/tool-schemas.json."""

import asyncio
import json
import sys
from pathlib import Path

from community_bridge.server import create_server


async def main() -> None:
    server = create_server(":memory:", None)
    tools = await server.list_tools()
    data = [t.model_dump(by_alias=True, exclude_none=True) for t in tools]
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/tool-schemas.json")
    out.write_text(json.dumps(data, indent=2) + "\n")
    print(f"wrote {len(data)} tool definitions to {out}")


asyncio.run(main())
