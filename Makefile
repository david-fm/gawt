.PHONY: install-skill uninstall-skill test lint build

install-skill:
	bash scripts/install-skill.sh

uninstall-skill:
	rm -rf ~/.agents/skills/gitagent

test:
	. .venv/bin/activate && pytest

lint:
	. .venv/bin/activate && ruff check src tests

build:
	. .venv/bin/activate && uv build
