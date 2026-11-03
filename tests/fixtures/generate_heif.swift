// Synthetic, non-private fixture. Encode real HEVC-in-HEIF with Apple ImageIO.
// Run: swift generate_heif.swift /tmp/pilot-heif
import Foundation
import ImageIO
import CoreGraphics
let directory = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
let w = 160, h = 96
let space = CGColorSpace(name: CGColorSpace.sRGB)!
let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8,
                    bytesPerRow: w * 4, space: space, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)!
// Top left red; top right green; bottom left blue; bottom right yellow.
for (x,y,color) in [(0,48,[1.0,0.0,0.0,1.0]), (80,48,[0.0,1.0,0.0,1.0]),
                     (0,0,[0.0,0.0,1.0,1.0]), (80,0,[1.0,1.0,0.0,1.0])] {
    ctx.setFillColor(CGColor(colorSpace: space, components: color.map { CGFloat($0) })!)
    ctx.fill(CGRect(x:x,y:y,width:80,height:48))
}
let image = ctx.makeImage()!
for orientation in 1...8 {
    let url = directory.appendingPathComponent("orientation-\(orientation).heic")
    let dest = CGImageDestinationCreateWithURL(url as CFURL, "public.heic" as CFString, 1, nil)!
    let properties: [CFString: Any] = [kCGImagePropertyOrientation: orientation,
        kCGImageDestinationLossyCompressionQuality: 0.95,
        kCGImagePropertyExifDictionary: [kCGImagePropertyExifUserComment:"synthetic metadata must not survive"],
        kCGImagePropertyGPSDictionary: [kCGImagePropertyGPSLatitude:0.0,kCGImagePropertyGPSLatitudeRef:"N",
                                       kCGImagePropertyGPSLongitude:0.0,kCGImagePropertyGPSLongitudeRef:"E"]]
    CGImageDestinationAddImage(dest,image,properties as CFDictionary)
    guard CGImageDestinationFinalize(dest) else { fatalError("HEIF encoding unavailable") }
}
