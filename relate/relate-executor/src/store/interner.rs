//! The append-only string interner behind label, relationship-type and
//! property-key ids (ADR-0033), and its plan-time [`TokenResolver`] seam
//! (ADR-0541).

use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::Arc;

use dashmap::DashMap;

#[derive(Debug, Default)]
pub(crate) struct Interner {
    to_id: DashMap<Arc<str>, u32>,
    /// Dense, append-only id→string table. Entries are never removed or
    /// reassigned, so a read for a committed id is a plain shard lookup; only
    /// `intern` mutates (on first sight of a string).
    to_str: DashMap<u32, Arc<str>>,
    /// Monotonic id source. Separate from `to_str.len()` so two concurrent
    /// first-time interns of distinct strings cannot observe the same len.
    next_id: AtomicU32,
    /// Bytes of distinct strings. `Arc` clones are not added again.
    bytes: std::sync::atomic::AtomicU64,
    /// One label vector per distinct label-id list. Walk stubs clone the
    /// `Arc` instead of rebuilding the strings.
    label_vecs: DashMap<Vec<u32>, Arc<Vec<String>>>,
}

#[cfg(test)]
thread_local! {
    /// `Interner::get_id` calls on this thread — test-only, read through
    /// [`take_get_id_calls`].
    static GET_ID_CALLS: std::cell::Cell<u64> = const { std::cell::Cell::new(0) };
}

/// Returns and resets this thread's `Interner::get_id` call count.
#[cfg(test)]
pub(crate) fn take_get_id_calls() -> u64 {
    GET_ID_CALLS.with(|c| c.replace(0))
}

impl relate_names::TokenResolver for Interner {
    fn resolve(&self, name: &str) -> relate_names::Token {
        match self.get_id(name) {
            Some(id) => relate_names::Token::Id(relate_names::TokenId(id)),
            None => relate_names::Token::Absent,
        }
    }
}

impl Interner {
    pub(crate) fn intern(&self, s: &str) -> u32 {
        if let Some(id) = self.to_id.get(s) {
            return *id;
        }
        // `entry` holds the shard write lock, so a concurrent `intern` of the
        // same string takes the occupied arm and both callers get one id.
        match self.to_id.entry(Arc::from(s)) {
            dashmap::mapref::entry::Entry::Occupied(e) => *e.get(),
            dashmap::mapref::entry::Entry::Vacant(e) => {
                let arc = e.key().clone();
                let id = self.next_id.fetch_add(1, Ordering::Relaxed);
                // Publish id→str before the id is reachable via `to_id`, so a
                // `get(id)` that observes the id always finds the string.
                self.to_str.insert(id, arc);
                e.insert(id);
                self.bytes.fetch_add(
                    crate::memory::interned_string_bytes(s.len()),
                    Ordering::Relaxed,
                );
                id
            }
        }
    }

    pub(crate) fn bytes(&self) -> u64 {
        self.bytes.load(Ordering::Relaxed)
    }

    /// Distinct interned strings — the census harness's key count.
    #[cfg(feature = "research")]
    pub(crate) fn key_count(&self) -> usize {
        self.to_id.len()
    }

    pub(crate) fn get_id(&self, s: &str) -> Option<u32> {
        #[cfg(test)]
        GET_ID_CALLS.with(|c| c.set(c.get() + 1));
        self.to_id.get(s).map(|v| *v)
    }

    /// Reverse lookup of an interned id. Every reachable id was published
    /// by [`Interner::intern`] before use, so a miss is a bug, not data:
    /// panic instead of silently returning the empty string.
    pub(crate) fn get(&self, id: u32) -> Arc<str> {
        self.to_str
            .get(&id)
            .map(|v| Arc::clone(&*v))
            .expect("interned id is published before use")
    }

    /// The label strings for `ids`, shared with every other stub of the
    /// same label list.
    pub(crate) fn cached_labels(&self, ids: &[u32]) -> Arc<Vec<String>> {
        if let Some(hit) = self.label_vecs.get(ids) {
            return Arc::clone(hit.value());
        }
        let labels = Arc::new(
            ids.iter()
                .map(|id| String::from(&*self.get(*id)))
                .collect::<Vec<_>>(),
        );
        match self.label_vecs.entry(ids.to_vec()) {
            dashmap::mapref::entry::Entry::Occupied(entry) => Arc::clone(entry.get()),
            dashmap::mapref::entry::Entry::Vacant(entry) => {
                let string_bytes = labels.iter().map(|label| label.len() as u64).sum();
                self.bytes.fetch_add(
                    crate::memory::label_vec_cache_bytes(ids.len(), string_bytes),
                    Ordering::Relaxed,
                );
                entry.insert(Arc::clone(&labels));
                labels
            }
        }
    }
}
