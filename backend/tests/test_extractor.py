from app.core.pricing import Level
from app.ingest.extractor import extract_file
from app.models import PayloadKind


def by_line(stmts):
    return {s.line: s for s in stmts}


GO = b"""package h
func (s *S) Create(ctx context.Context, req *Req) error {
  s.logger.Infof("Entering Create")
  log.Infof("Request received: %+v", req)
  resp, err := s.client.Do(req)
  if err != nil {
    s.logger.Errorf("failed: %v", err)
    return err
  }
  logger.Infow("Response", "response", resp, "workspaceId", req.ID)
  for _, r := range items { log.Info("item", r.ID) }
  return nil
}
func (r *R) pollStatus() {
  for {
    log.Info("polling")
    time.Sleep(time.Second)
  }
}
func lookup() { if apierrors.IsNotFound(err) { log.Errorf("not found %s", name) } }
func (c *C) run() { c.logger.Error("x", zap.String("id", id), zap.Error(err)) }
"""


def test_go_levels_context_and_payloads():
    s = by_line(extract_file("r", "a.go", GO))
    assert s[3].entry_exit and s[3].level is Level.INFO
    assert s[4].payload_args[0].kind is PayloadKind.REQUEST
    assert s[7].in_error_path and s[7].propagates_error and s[7].guarded_calls == ["Do"]
    assert [p.kind for p in s[10].payload_args] == [PayloadKind.RESPONSE]  # workspaceId is an identifier
    assert s[11].in_loop and not s[11].payload_args
    assert s[16].in_polling and s[16].in_loop
    assert s[20].expected_condition == "not_found"


def test_zap_field_constructors_are_not_log_calls():
    stmts = extract_file("r", "a.go", GO)
    assert all(s.logger_call != "zap.Error" for s in stmts)
    assert len([s for s in stmts if s.line == 21]) == 1


JAVA = b"""class A {
 @KafkaListener(topics="x")
 void onMessage(Request request) {
   log.info("Request {}", request);
   logger.info("id=" + request.getId());
   try { svc.run(request); } catch (ValidationException e) { logger.error("invalid input", e); throw e; }
   log.info("Payload: " + objectMapper.writeValueAsString(request));
 }
}"""


def test_java_consumer_catch_and_json_blob():
    s = by_line(extract_file("r", "A.java", JAVA))
    assert s[4].in_consumer and s[4].payload_args[0].kind is PayloadKind.REQUEST
    assert not s[5].payload_args
    assert s[6].in_error_path and s[6].logs_exception and s[6].propagates_error
    assert s[6].expected_condition == "validation"
    assert s[7].payload_args[0].kind is PayloadKind.JSON_BLOB


PY = b"""
def run(req, resp):
    for i in range(3):
        logger.info("req %s", req)
    logger.info(f"response={resp} id={resp.id}")
    try:
        x()
    except KeyError as e:
        logger.error("missing key", exc_info=True)
    logging.warning("body %s", json.dumps(req.body))
"""


def test_python_loop_fstring_exc_info_and_json():
    s = by_line(extract_file("r", "a.py", PY))
    assert s[4].in_loop and s[4].payload_args[0].kind is PayloadKind.REQUEST
    assert s[5].payload_args[0].kind is PayloadKind.RESPONSE and "resp.id" in s[5].args
    assert s[9].in_error_path and s[9].logs_exception
    assert s[10].level is Level.WARNING and s[10].payload_args[0].kind is PayloadKind.JSON_BLOB


def test_identifiers_are_not_payloads():
    src = b'package a\nfunc f() { log.Infof("done id=%s count=%d took=%v", requestID, len(items), elapsed) }\n'
    assert extract_file("r", "a.go", src)[0].payload_args == []


def test_non_logger_receivers_ignored():
    src = b'package a\nfunc f() { fmt.Println("x"); cfg.Info("y"); catalog.Error("z") }\n'
    assert extract_file("r", "a.go", src) == []


def test_unsupported_language_returns_empty():
    assert extract_file("r", "a.rb", b'logger.info "x"') == []
