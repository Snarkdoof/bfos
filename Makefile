.PHONY: test simulation clean help

help:
	@echo "Bag full of spanners (BFOS) - Makefile"
	@echo "Available commands:"
	@echo "  make test       - Run the unit tests using 'uv run python'"
	@echo "  make simulation - Run the vehicle/driver simulation"
	@echo "  make clean      - Clean up temporary files, database files, and caches"

test:
	uv run python -m unittest discover -s tests

simulation:
	uv run python simulation.py

clean:
	rm -rf .pytest_cache .uv __pycache__ bfos/__pycache__ tests/__pycache__
	rm -f *.db *.db-wal *.db-shm
