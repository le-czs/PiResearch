from constellation.node_size import SIZE_STEP, apply_call_radii, radius_for_calls


def test_more_tool_calls_make_a_larger_node():
    assert radius_for_calls(0) < radius_for_calls(1) < radius_for_calls(8)
    assert radius_for_calls(8) < radius_for_calls(40)
    assert radius_for_calls(86) - radius_for_calls(6) >= 40


def test_goal_stays_one_size_above_the_busiest_node():
    quiet = {"id": "t1", "goal": False, "steps": [], "live": []}
    busy = {
        "id": "t2",
        "goal": False,
        "steps": [{"tool": "web_search"}] * 6,
        "live": [{"tool": "web_fetch"}, {"tool": "思考"}],
    }
    goal = {"id": "goal", "goal": True, "steps": [{"tool": "web_search"}] * 20, "live": []}
    apply_call_radii([goal, quiet, busy])
    assert busy["radius"] > quiet["radius"]
    assert busy["radius"] == radius_for_calls(7)
    assert goal["radius"] == round(busy["radius"] + SIZE_STEP, 1)
    assert goal["radius"] > busy["radius"]


def test_goal_is_one_size_above_an_empty_graph():
    goal = {"id": "goal", "goal": True, "steps": [], "live": []}
    apply_call_radii([goal])
    assert goal["radius"] == round(radius_for_calls(0) + SIZE_STEP, 1)
