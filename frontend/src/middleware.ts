import { createHash, timingSafeEqual } from "node:crypto";

import { NextResponse, type NextRequest } from "next/server";

/**
 * Optional HTTP Basic Auth for the whole site, including the /api proxy.
 * Enabled when BASIC_AUTH_USER and BASIC_AUTH_PASSWORD are set (read at runtime, so the
 * Node.js middleware runtime is required). StreamFeed has no accounts, so this is what
 * keeps a deployed instance private.
 */
export const config = {
  runtime: "nodejs",
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};

const digest = (value: string) => createHash("sha256").update(value).digest();

function matches(given: string, expected: string): boolean {
  return timingSafeEqual(digest(given), digest(expected));
}

export function middleware(request: NextRequest) {
  const user = process.env.BASIC_AUTH_USER;
  const password = process.env.BASIC_AUTH_PASSWORD;
  if (!user || !password) return NextResponse.next();

  const header = request.headers.get("authorization") ?? "";
  if (header.startsWith("Basic ")) {
    const decoded = Buffer.from(header.slice(6), "base64").toString("utf8");
    const sep = decoded.indexOf(":");
    if (sep >= 0) {
      const userOk = matches(decoded.slice(0, sep), user);
      const passwordOk = matches(decoded.slice(sep + 1), password);
      if (userOk && passwordOk) return NextResponse.next();
    }
  }
  return new NextResponse("Sign in to view StreamFeed.", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="StreamFeed", charset="UTF-8"' },
  });
}
