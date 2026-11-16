import AppKit

@MainActor func continuationConsentChecks() throws -> Int {
    var checks = 0
    func check(_ ok: Bool, _ message: String) {
        precondition(ok, message); checks += 1
    }
    let params: [String: Any] = ["challenge": "continuation:synthetic", "digest": String(repeating: "a", count: 64),
                                "text": "Verify only this synthetic result", "_deadline": 200.0]
    let receipt = try ContinuationConsent.responseReceipt(.alertFirstButtonReturn, params: params, now: 100)
    check(receipt["approved"] as? Bool == true, "Explicit native acceptance missing")
    check(receipt["method"] as? String == "native_scope_confirmation", "Must not claim OS identity proof")
    check(receipt["challenge"] as? String == params["challenge"] as? String, "Challenge changed")
    check(receipt["digest"] as? String == params["digest"] as? String, "Scope changed")
    for response in [NSApplication.ModalResponse.alertSecondButtonReturn, .abort, .cancel, .stop] {
        do {
            _ = try ContinuationConsent.responseReceipt(response, params: params, now: 100)
            preconditionFailure("Dismissal minted a grant")
        } catch { checks += 1 }
    }
    for now in [200.0, 201.0] {
        do {
            _ = try ContinuationConsent.responseReceipt(.alertFirstButtonReturn, params: params, now: now)
            preconditionFailure("Expired confirmation minted a grant")
        } catch { checks += 1 }
    }
    for key in ["challenge", "digest", "text", "_deadline"] {
        var missing = params; missing.removeValue(forKey: key)
        do {
            _ = try ContinuationConsent.responseReceipt(.alertFirstButtonReturn, params: missing, now: 100)
            preconditionFailure("Incomplete scope minted a grant")
        } catch { checks += 1 }
    }
    return checks
}
