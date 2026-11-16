import { Suspense } from "react";

import { FeedView } from "@/components/feed/FeedView";

export default function FeedPage() {
  // useSearchParams needs a Suspense boundary for static rendering.
  return (
    <Suspense>
      <FeedView />
    </Suspense>
  );
}
