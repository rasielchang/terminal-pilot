// Decode only the primary HEIF still image using the host's ImageIO codecs.
// This is a bounded subprocess, never an App/Terminal or a network converter.
import Foundation
import ImageIO
import CoreGraphics
import Darwin

func fail(_ code: String) -> Never {
    FileHandle.standardError.write(Data((code + "\n").utf8)); exit(2)
}
guard CommandLine.arguments.count == 3 else { fail("arguments") }
let input = URL(fileURLWithPath: CommandLine.arguments[1])
let output = URL(fileURLWithPath: CommandLine.arguments[2])
guard let size = try? input.resourceValues(forKeys: [.fileSizeKey]).fileSize,
      size > 0, size <= 12 * 1024 * 1024,
      let source = CGImageSourceCreateWithURL(input as CFURL, [kCGImageSourceShouldCache: false] as CFDictionary),
      let type = CGImageSourceGetType(source) as String?,
      ["public.heic", "public.heif"].contains(type) else { fail("format") }
let index = CGImageSourceGetPrimaryImageIndex(source)
guard index < CGImageSourceGetCount(source),
      let props = CGImageSourceCopyPropertiesAtIndex(source, index, nil) as? [CFString: Any],
      let width = (props[kCGImagePropertyPixelWidth] as? NSNumber)?.intValue,
      let height = (props[kCGImagePropertyPixelHeight] as? NSNumber)?.intValue,
      width > 0, height > 0 else { fail("decode") }
guard width <= 12000, height <= 12000, width * height <= 50_000_000 else { fail("dimensions") }
let options: [CFString: Any] = [
    kCGImageSourceCreateThumbnailFromImageAlways: true,
    kCGImageSourceCreateThumbnailWithTransform: true,
    kCGImageSourceThumbnailMaxPixelSize: 3072,
    kCGImageSourceShouldCacheImmediately: true,
    kCGImageSourceShouldAllowFloat: false,
]
guard let decoded = CGImageSourceCreateThumbnailAtIndex(source, index, options as CFDictionary),
      decoded.width > 0, decoded.height > 0, max(decoded.width, decoded.height) <= 3072,
      let provider = decoded.dataProvider, let pixels = provider.data,
      CFDataGetLength(pixels) >= decoded.bytesPerRow * decoded.height,
      let verifiedProvider = CGDataProvider(data: pixels),
      let verifiedImage = CGImage(width: decoded.width, height: decoded.height,
          bitsPerComponent: decoded.bitsPerComponent, bitsPerPixel: decoded.bitsPerPixel,
          bytesPerRow: decoded.bytesPerRow, space: decoded.colorSpace ?? CGColorSpaceCreateDeviceRGB(),
          bitmapInfo: decoded.bitmapInfo, provider: verifiedProvider, decode: nil,
          shouldInterpolate: true, intent: .defaultIntent),
      let space = CGColorSpace(name: CGColorSpace.sRGB),
      let context = CGContext(data: nil, width: decoded.width, height: decoded.height,
                              bitsPerComponent: 8, bytesPerRow: decoded.width * 4,
                              space: space, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { fail("decode") }
let bounds = CGRect(x: 0, y: 0, width: decoded.width, height: decoded.height)
context.setFillColor(CGColor(gray: 1, alpha: 1)); context.fill(bounds)
context.draw(verifiedImage, in: bounds)
guard let image = context.makeImage(),
      let destination = CGImageDestinationCreateWithURL(output as CFURL, "public.png" as CFString, 1, nil) else { fail("decode") }
// Creating a new destination from pixels excludes source EXIF/GPS/XMP/auxiliary data.
CGImageDestinationAddImage(destination, image, nil)
guard CGImageDestinationFinalize(destination) else { fail("decode") }
