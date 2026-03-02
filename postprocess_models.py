# Copyright 2026 UCP Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import re
from pathlib import Path
import sys


def postprocess_file(file_path):
  with open(file_path, "r") as f:
    content = f.read()

  if "RootModel" not in content:
    return False

  # Find classes inheriting from RootModel.
  # Handles both single line and multiline class definitions.
  # Match: class Name(RootModel[...]):
  pattern = re.compile(r"(class\s+\w+\(\s*RootModel\[.*?\]\s*\):)", re.DOTALL)

  offset = 0
  new_content = content
  changed = False

  for match in pattern.finditer(content):
    header = match.group(1)
    # Check if model_config is already in the class body (simple heuristic)
    # We look a bit ahead for model_config
    body_snippet = content[match.end() : match.end() + 200]
    if "model_config =" in body_snippet:
      continue

    # Insertion with 4-space indentation to match datamodel-codegen's initial output.
    insertion = "\n    model_config = ConfigDict(\n        frozen=True,\n    )"

    new_content = (
      new_content[: match.end() + offset]
      + insertion
      + new_content[match.end() + offset :]
    )
    offset += len(insertion)
    changed = True

  if changed:
    # Ensure ConfigDict is in the pydantic import line
    import_match = re.search(r"from pydantic import ([^\n]+)", new_content)
    if import_match:
      imports_str = import_match.group(1)
      if "ConfigDict" not in imports_str:
        # Re-format the import line
        imports = [
          i.strip()
          for i in imports_str.replace("(", "").replace(")", "").split(",")
        ]
        if "ConfigDict" not in imports:
          imports.append("ConfigDict")
        imports = sorted([i for i in imports if i])
        new_import_line = f"from pydantic import {', '.join(imports)}"
        new_content = new_content.replace(
          import_match.group(0), new_import_line
        )

    with open(file_path, "w") as f:
      f.write(new_content)
    return True
  return False


def main():
  output_dir = "src/ucp_sdk/models/schemas"
  if len(sys.argv) > 1:
    output_dir = sys.argv[1]

  base_dir = Path(output_dir)
  if not base_dir.exists():
    print(f"Directory {output_dir} does not exist.")
    return

  for py_file in base_dir.rglob("*.py"):
    if postprocess_file(py_file):
      print(f"Post-processed {py_file}")


if __name__ == "__main__":
  main()
