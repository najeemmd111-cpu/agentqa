.PHONY: install test lint demo-api run-demo docker
install:
	pip install -e ".[dev]"
test:
	pytest
lint:
	ruff check . && ruff format --check .
demo-api:          ## start the buggy target on :9000
	uvicorn demo_target.app:app --port 9000
run-demo:          ## run the agent against it (needs `make demo-api` in another shell)
	python -m agentqa.cli run --spec demo_target/openapi.yaml --base-url http://localhost:9000 --auth "Bearer demo-token"
docker:
	docker compose up --build
