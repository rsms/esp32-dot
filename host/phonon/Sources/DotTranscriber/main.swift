import Foundation
import PhononCoreML

// Private stdin/stdout JSON-lines worker. Keep the CoreML model resident between
// utterances; diagnostics go to stderr so they cannot corrupt the protocol.
func emit(_ value: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) else { return }
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data([10]))
}

guard CommandLine.arguments.count == 2 else {
    fputs("usage: DotTranscriber MODEL_DIRECTORY\n", stderr)
    exit(2)
}
do {
    var options = Transcriber.Options()
    options.eagerFunctions = [5, 10, 15, 35]
    let model = try Transcriber(bundle: URL(fileURLWithPath: CommandLine.arguments[1]), options: options)
    _ = try model.transcribe([Float](repeating: 0, count: 16000 * 3))
    emit(["ready": true, "load_seconds": model.loadSeconds])
    while let line = readLine() {
        var id = ""
        do {
            guard let data = line.data(using: .utf8),
                  let request = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let requestID = request["id"] as? String,
                  let path = request["path"] as? String else {
                throw NSError(domain: "DotTranscriber", code: 1)
            }
            id = requestID
            let began = Date()
            let result = try model.transcribe(url: URL(fileURLWithPath: path))
            emit(["id": id, "text": result.text, "audio_seconds": result.audioSeconds,
                  "transcribe_seconds": Date().timeIntervalSince(began),
                  "words": result.words.map { ["text": $0.text, "start": $0.start, "end": $0.end] as [String: Any] }])
        } catch {
            emit(["id": id, "error": String(describing: error)])
        }
    }
} catch {
    fputs("Phonon model initialization failed: \(error)\n", stderr)
    exit(1)
}
