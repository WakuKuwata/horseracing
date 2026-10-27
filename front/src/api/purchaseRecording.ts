import { useQueryClient } from "@tanstack/react-query";

import { usePauseState } from "./opsStatus";
import { submitPurchaseRecord } from "./opsClient";
import { usePurchaseRecords } from "./queries";
import type { PurchaseRecordView } from "./types";

/**
 * Feature 106 recording state for ONE race, shared by the legacy slip panel (comparison tab) and
 * the ROI-view record section (B6). One effective record per race: both surfaces read the same
 * query and go through the same submit, so a record made in one place is seen by the other.
 */
export function usePurchaseRecording(raceId: string, raceDate: string | undefined) {
  const queryClient = useQueryClient();
  const { paused } = usePauseState();
  // Effective record after fold — a voided race legitimately comes back recordable. Range = the
  // race's own day.
  const recordsQuery = usePurchaseRecords(
    { from: raceDate ?? "", to: raceDate ?? "" },
    { enabled: !!raceDate },
  );
  const existingView: PurchaseRecordView | null =
    recordsQuery.data?.records.find((r) => r.race_id === raceId) ?? null;

  async function submit(payload: Parameters<typeof submitPurchaseRecord>[0]) {
    const result = await submitPurchaseRecord(payload);
    await queryClient.invalidateQueries({ queryKey: ["purchase-records"] });
    await queryClient.invalidateQueries({ queryKey: ["purchase-comparison"] });
    return { record_id: result.purchase_record_id };
  }

  return { existingView, submit, paused, isLoading: recordsQuery.isLoading };
}
