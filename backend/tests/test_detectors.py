from pathlib import Path

from app.analysis.analyzer import analyze_statements
from app.core.pricing import price_per_gb
from app.ingest.extractor import extract_file
from app.models import FindingType


def analyze(settings, src: str, name="x.go", lang_ext=None):
    stmts = extract_file("repo", name, src.encode())
    return analyze_statements(settings, "repo", stmts)


def types(a):
    return {f.type for f in a.findings}


def test_rule3_payload_logging_is_highest_priority(settings):
    a = analyze(settings, 'package a\nfunc f(request *R) { log.Infof("Request %+v", request) }\n')
    f = a.findings[0]
    assert f.type is FindingType.REQUEST_PAYLOAD and f.rule == 3
    assert "requestId" in f.suggested_code and "request" in f.recommendation


def test_rule1_entry_exit_never_flagged(settings):
    src = (
        'package a\nfunc (r *R) poll() {\n for {\n  log.Infof("Entering poll")\n  time.Sleep(time.Second)\n'
        '  log.Infof("Exiting poll")\n }\n}\n'
    )
    assert analyze(settings, src).findings == []


def test_rule2_debug_costs_nothing_and_is_never_recommended(settings):
    src = 'package a\nfunc f(request *R) { for { log.Debugf("req %+v", request); time.Sleep(time.Second) } }\n'
    a = analyze(settings, src)
    assert a.findings == [] and a.cost.gb_per_day == 0


def test_rule2_debug_enabled_in_prod_is_billable(settings):
    s = settings.model_copy(update={"debug_enabled_in_prod": True})
    src = 'package a\nfunc f(request *R) { log.Debugf("req %+v", request) }\n'
    assert analyze(s, src).findings


def test_info_is_never_recommended_to_become_debug(settings):
    src = 'package a\nfunc (r *R) poll() { for { log.Infof("polling"); time.Sleep(time.Second) } }\n'
    a = analyze(settings, src)
    assert a.findings
    for f in a.findings:
        assert "to DEBUG" not in f.recommendation.replace("downgrade to DEBUG automatically", "")


def test_rule5_validation_error_is_misclassified(settings):
    src = 'package a\nfunc f() {\n if err != nil {\n  log.Errorf("invalid request: %v", err)\n  return\n }\n}\n'
    a = analyze(settings, src)
    f = next(f for f in a.findings if f.type is FindingType.MISCLASSIFIED_ERROR)
    assert "WARNING" in f.recommendation and f.est_savings_usd_month > 0


def test_unexpected_error_is_left_alone(settings):
    src = (
        'package a\nfunc f() {\n if err != nil {\n  log.Errorf("database write failed: %v", err)\n  return err\n }\n}\n'
    )
    assert FindingType.MISCLASSIFIED_ERROR not in types(analyze(settings, src))


def test_fatal_critical_is_legitimate(settings):
    src = 'package a\nfunc f() {\n if err != nil {\n  log.Fatalf("cannot start: %v", err)\n }\n}\n'
    assert FindingType.MISCLASSIFIED_CRITICAL not in types(analyze(settings, src))


def test_rule6_service_and_controller_duplicate(settings):
    src = (
        "package a\n"
        'func (s *S) Create() error {\n err := s.store.Save()\n if err != nil {\n  s.log.Errorf("save failed: %v", err)\n  return err\n }\n return nil\n}\n'
        'func (h *H) Handle() error {\n err := s.Create()\n if err != nil {\n  h.log.Errorf("create failed: %v", err)\n  return err\n }\n return nil\n}\n'
    )
    a = analyze(settings, src)
    dup = next(f for f in a.findings if f.type is FindingType.DUPLICATE_LOGGING)
    assert dup.rule == 6 and dup.related and dup.function == "Create"


def test_rule7_loop_summary_recommended(settings):
    src = 'package a\nfunc f(items []I) {\n for _, it := range items {\n  log.Infof("handled %s", it.ID)\n }\n}\n'
    assert FindingType.LOOP_LOGGING in types(analyze(settings, src))


def test_rule4_low_value_only_when_hot_or_repeated(settings):
    quiet = 'package a\nfunc f() { log.Infof("Starting operation") }\n'
    hot = 'package a\nfunc f(xs []int) { for range xs { log.Infof("Processing event") } }\n'
    assert FindingType.LOW_VALUE not in types(analyze(settings, quiet))
    assert FindingType.LOW_VALUE in types(analyze(settings, hot))


def test_rule8_json_blob(settings):
    src = 'package a\nfunc f(p map[string]any) { b, _ := json.Marshal(p); log.Infof("payload %s", string(b)) }\n'
    assert FindingType.JSON_BLOB in types(analyze(settings, src))


def test_pricing_matches_spec():
    # Prices are fetched live from IBM Global Catalog — check ordering and plausible ranges
    # rather than exact values so the test stays valid after IBM price updates.
    p_error_7 = price_per_gb("ERROR", 7)
    p_critical_90 = price_per_gb("CRITICAL", 90)
    p_info = price_per_gb("INFO")
    p_warning = price_per_gb("WARNING")

    # Priority Insights > Analyze & Alert > Store & Search
    assert p_error_7 > p_info > p_warning, "pipeline cost ordering violated"
    # Priority Insights 90d > 7d
    assert p_critical_90 > p_error_7, "longer retention must cost more"
    # Plausible USD/GB ranges (sanity check, not exact)
    assert 0.30 < p_warning < 2.0, f"Store & Search price out of range: {p_warning}"
    assert 0.50 < p_info < 3.0, f"Analyze & Alert price out of range: {p_info}"
    assert 0.80 < p_error_7 < 5.0, f"Priority Insights 7d price out of range: {p_error_7}"


def test_retention_changes_priority_insights_cost(settings):
    from app.analysis.cost import CostModel
    from app.core.pricing import price_per_gb

    stmts = extract_file("repo", "x.go", b'package a\nfunc f() { if err != nil { log.Errorf("boom %v", err) } }\n')
    cheap = CostModel(settings, stmts).usd_month(stmts[0])
    dear = CostModel(settings.model_copy(update={"priority_retention_days": 90}), stmts).usd_month(stmts[0])
    # Use live prices for the ratio so the test stays valid after price updates
    expected_ratio = price_per_gb("ERROR", 90) / price_per_gb("ERROR", 7)
    assert abs(dear / cheap - expected_ratio) < 1e-6


def test_savings_never_exceed_cost(settings):
    src = 'package a\nfunc (r *R) poll(ws *W) { for { log.Infof("poll %+v", ws); time.Sleep(time.Second) } }\n'
    a = analyze(settings, src)
    assert 0 < a.potential_high_pct <= 60 and a.potential_low_pct <= a.potential_high_pct


def test_sample_repo_findings_use_real_files(platform):
    a = platform.state("icl-test-schematics-api").analysis
    assert a.risk_level.value == "HIGH"
    assert Path(platform.registry.get("icl-test-schematics-api").root / "workflow/reconciler.go").is_file()
    assert {"workflow/reconciler.go", "handlers/resource_handler.go"} <= {f.file for f in a.findings}
    # cost reports use old repo names; icl-test-* repos fall back to estimated basis
    assert a.cost.basis in ("calibrated", "estimated")
