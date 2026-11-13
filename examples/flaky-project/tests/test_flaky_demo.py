from pathlib import Path


def test_alternates_between_pass_and_failure():
    state = Path(__file__).parents[1] / ".qualitybridge-demo" / "counter.txt"
    state.parent.mkdir(parents=True, exist_ok=True)
    count = int(state.read_text() or "0") if state.exists() else 0
    count += 1
    state.write_text(str(count))

    assert count % 2 == 0, f"Falha intencional reproduzível na execução {count}."
