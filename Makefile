COMPOSE_FILE=deploy/docker-compose.yml

.PHONY: up down logs api worker frontend test

up:
	docker compose -f $(COMPOSE_FILE) up --build -d

down:
	docker compose -f $(COMPOSE_FILE) down

logs:
	docker compose -f $(COMPOSE_FILE) logs -f

api:
	docker compose -f $(COMPOSE_FILE) up --build -d api worker-supervisor postgres

worker:
	docker compose -f $(COMPOSE_FILE) up --build -d worker-supervisor

frontend:
	docker compose -f $(COMPOSE_FILE) up --build -d frontend

test:
	pytest -q
