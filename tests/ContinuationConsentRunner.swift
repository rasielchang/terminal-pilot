import Foundation

// The isolated runner only needs the production error's message interface.
struct PilotError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

@main struct ContinuationConsentRunner {
    @MainActor static func main() throws {
        print("Passed \(try continuationConsentChecks()) native consent checks; no real dialog or OS authentication invoked.")
    }
}
