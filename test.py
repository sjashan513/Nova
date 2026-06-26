"""
Fase 2: Director writing a real file to the CURRENT working directory,
via a hand-written Plan -- no Kimi, no mocks. This is the first time
filesystem.write runs INSIDE a full Director.run() pipeline (T5 tested
write() in isolation; T8 and the stress test only ever read).

Uses os.getcwd() genuinely -- wherever you run this script FROM is
where the file gets written. Run it from your repo root (or anywhere
else) to see it write there for real:

    python test_fase2_write.py
"""

import os

from core.domain.models import Plan, Step
from core.director.director_instance import DirectorInstance

OUTPUT_FILENAME = "nova_hello_world.txt"


def make_plan(target_path: str) -> Plan:
    return Plan(
        objective="Write a hello world file to the current directory",
        steps=[
            Step(
                id="s1",
                description="Write hello world to the current directory",
                tool_or_worker="filesystem",
                action="write",
                input={
                    "path": target_path,
                    "content": "Hello, world -- written by Nova's Director.\n",
                },
            ),
            Step(
                id="s2",
                description="Read it back to confirm what was actually written",
                tool_or_worker="filesystem",
                action="read",
                depends_on=["s1"],
                # References s1's OWN reported path, not a hardcoded
                # string -- proves the Director is reading back exactly
                # what it itself just wrote, via real $step_id.field
                # resolution, not a coincidentally-matching literal.
                input={"path": "$s1.path"},
            ),
            Step(
                id="s3",
                description="List the current directory to show the file is really there",
                tool_or_worker="filesystem",
                action="list",
                depends_on=["s1"],
                input={"path": "."},
            ),
        ],
    )


def main():
    cwd = os.getcwd()
    target_path = os.path.join(cwd, OUTPUT_FILENAME)

    print(f"Current directory: {cwd}")
    print(f"Target file:        {target_path}")
    print()

    plan = make_plan(target_path)
    director = DirectorInstance(plan, plan_id="write-hello-world")
    result = director.run()

    assert director.status == "DONE"

    write_result = result["context"]["s1"]
    read_result = result["context"]["s2"]
    list_result = result["context"]["s3"]

    print(f"s1 (write) -> path: {write_result['path']!r}, "
          f"bytes_written: {write_result['bytes_written']}")
    print(
        f"s2 (read back, via \"$s1.path\") -> content: {read_result['content']!r}")
    print(f"s3 (list cwd) -> {OUTPUT_FILENAME!r} in entries: "
          f"{OUTPUT_FILENAME in list_result['entries']}")

    assert read_result["content"] == "Hello, world -- written by Nova's Director.\n"
    assert OUTPUT_FILENAME in list_result["entries"]
    assert os.path.exists(target_path)

    print()
    print(f"CONFIRMED: real file written to {target_path}, read back through")
    print("           real $step_id.field resolution (not a hardcoded path),")
    print("           and confirmed present via a real directory listing.")
    print()
    print(f"You can inspect it yourself: cat {target_path}")
    print(f"(or delete it: rm {target_path})")


if __name__ == "__main__":
    main()
