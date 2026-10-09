// swift-tools-version:6.0
import PackageDescription

let package = Package(
    name: "DotTranscriber",
    platforms: [.macOS(.v15)],
    dependencies: [.package(url: "https://github.com/fermionresearch/phonon-coreml", exact: "1.1.2")],
    targets: [.executableTarget(name: "DotTranscriber", dependencies: [
        .product(name: "PhononCoreML", package: "phonon-coreml")
    ])]
)
