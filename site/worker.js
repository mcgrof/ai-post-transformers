export default {
  async fetch(request, env) {
    const url = new URL(request.url)

    let key = url.pathname === "/" ? "index.html" : url.pathname.slice(1)

    // Support Range requests for audio seeking
    const rangeHeader = request.headers.get("Range")
    let object

    if (rangeHeader) {
      // Parse "bytes=START-END" format
      const match = rangeHeader.match(/bytes=(\d+)-(\d*)/)
      if (match) {
        const start = parseInt(match[1])
        const end = match[2] ? parseInt(match[2]) : undefined

        // R2 expects { offset, length } or { offset, suffix }
        const range = end !== undefined
          ? { offset: start, length: end - start + 1 }
          : { offset: start }

        object = await env.BUCKET.get(key, { range })
      } else {
        object = await env.BUCKET.get(key)
      }
    } else {
      object = await env.BUCKET.get(key)
    }

    if (!object) {
      return new Response("Not found", { status: 404 })
    }

    const headers = new Headers()
    object.writeHttpMetadata(headers)
    headers.set("Accept-Ranges", "bytes")

    if (rangeHeader && object.range) {
      const r = object.range
      const offset = r.offset || 0
      const length = r.length || (object.size - offset)
      const end = offset + length - 1
      headers.set("Content-Range", `bytes ${offset}-${end}/${object.size}`)
      headers.set("Content-Length", String(length))
      return new Response(object.body, { status: 206, headers })
    }

    headers.set("Content-Length", String(object.size))
    return new Response(object.body, { headers })
  }
}
