import AppKit
let image = NSImage(size: NSSize(width: 1024, height: 1024))
image.lockFocus()
NSColor(red: 0.10, green: 0.13, blue: 0.17, alpha: 1).setFill()
NSBezierPath(roundedRect: NSRect(x: 50, y: 50, width: 924, height: 924), xRadius: 200, yRadius: 200).fill()
NSColor(red: 0.28, green: 0.88, blue: 0.73, alpha: 1).setStroke()
for box in [NSRect(x: 200, y: 300, width: 440, height: 385), NSRect(x: 390, y: 465, width: 440, height: 285)] {
    let path = NSBezierPath(roundedRect: box, xRadius: 38, yRadius: 38)
    path.lineWidth = 26
    NSColor(red: 0.10, green: 0.13, blue: 0.17, alpha: 1).setFill(); path.fill(); path.stroke()
}
let arrow = NSBezierPath(); arrow.move(to: NSPoint(x: 452, y: 660)); arrow.line(to: NSPoint(x: 502, y: 610)); arrow.line(to: NSPoint(x: 452, y: 560)); arrow.lineWidth = 24; arrow.stroke()
let underline = NSBezierPath(); underline.move(to: NSPoint(x: 550, y: 550)); underline.line(to: NSPoint(x: 650, y: 550)); underline.lineWidth = 24; underline.stroke()
image.unlockFocus()
let bitmap = NSBitmapImageRep(data: image.tiffRepresentation!)!
try bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
