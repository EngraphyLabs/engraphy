// Pending-write watcher (pure, no vscode import, so test-client can run it).
//
// A write that lands in the dedup band is parked by the server for 24 hours and
// needs a person to resolve it. This module turns successive pending_list reads
// into the two facts the editor surfaces: how many rows are live right now (the
// status-bar count and the view badge), and which of them have not been seen
// before (the notification).
//
// Contract with the caller: observe() is called ONLY with the result of a
// successful read. A failed read must leave the watcher untouched, because
// forgetting the seen set on a network blip would announce every row again the
// moment the server comes back.
//
// Expired rows are dropped before anything is counted or diffed. pending_list
// lists only unexpired rows, but a row can expire between the server's read and
// this one, and an older server lists expired rows too. Only Dismiss applies to
// such a row, so it is not something to announce. pending_list orders newest
// first, so the newest rows are always inside the read window.

import { isExpired, type PendingListItem } from './toolResult';

export interface PendingObservation {
	/** Rows that have not expired. */
	active: number;
	/** Ids of active rows that no earlier observation contained. */
	fresh: string[];
}

export class PendingWatch {
	private seen = new Set<string>();
	private observed = false;

	/**
	 * True once a successful read has been observed. The first observation of a
	 * session seeds the seen set: rows already waiting at startup are counted,
	 * and only rows that arrive after it are announced.
	 */
	get primed(): boolean {
		return this.observed;
	}

	observe(items: PendingListItem[], now: number = Date.now()): PendingObservation {
		const active = items.filter((i) => i.id.length > 0 && !isExpired(i, now));
		const ids = new Set(active.map((i) => i.id));
		const fresh = [...ids].filter((id) => !this.seen.has(id));
		// Replacing the set prunes every id that is gone (resolved or expired), so
		// it never grows past one read's worth of rows.
		this.seen = ids;
		this.observed = true;
		return { active: ids.size, fresh };
	}

	/** Forget everything, for a different server or identity. */
	reset(): void {
		this.seen = new Set<string>();
		this.observed = false;
	}
}

function writes(n: number): string {
	return n === 1 ? '1 memory write' : `${n} memory writes`;
}

/** Notification copy for `n` rows waiting. */
export function pendingMessage(n: number): string {
	return `${writes(n)} ${n === 1 ? 'is' : 'are'} waiting for your review.`;
}

/** Status-bar tooltip and badge tooltip for `n` rows waiting. */
export function pendingTooltip(n: number): string {
	return `${writes(n)} waiting for review. Click to review.`;
}
