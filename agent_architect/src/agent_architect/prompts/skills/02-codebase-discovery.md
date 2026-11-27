Use this to learn the existing system with `list_repo_files` and `read_repo_file`.

1. List the repository root. Read the README and the build or dependency files
   (package.json, pyproject.toml, requirements.txt, *.csproj, pom.xml,
   go.mod, Dockerfile, docker-compose.yml) to identify languages, frameworks,
   databases and how the system is deployed.
2. Read `.platform/init/` documentation when it exists; it describes the
   components, business behaviour and conventions. Prefer it over guessing.
3. Find the entry points (main files, app/router setup, controllers, pages)
   and follow them one or two levels to see how a request flows.
4. Note the conventions to follow: folder layout, naming, layering (for
   example router -> service -> repository), error handling, configuration,
   authentication, testing.
5. Identify what the feature can reuse and what it must change. Do not read
   every file: stop when you can name the components involved and their
   responsibilities with evidence.
