//! The sealed arena's write-path invariants: the per-event charge's
//! price_side arms (the one formula the admission estimate prices delete
//! batches with), and the fully-reclaimed chunk's dead stream.

use relate_core::Value;

use super::super::Graph;

/// The reposition's sealed charge follows price_side's arms — the one
/// formula the admission estimate prices delete batches with: a second
/// reposition of an already-tombstoned position prices the in-place arm's
/// zero, and a ts update on a type with no sealed entry prices nothing
/// (its delta-only arm records no tombstone). The recount agrees after
/// each write.
#[test]
fn reposition_sealed_charge_prices_the_price_side_arms() {
    let g = Graph::default();
    let (a, b, ids, _) = crate::graph::test_support::folded_floor_chunk(&g);

    // A second reposition of the same sealed hop: the tombstone exists,
    // the in-place arm prices zero.
    g.begin_write().unwrap();
    g.set_property_rel(ids[0], "ts", Value::Int(1000));
    g.commit_write();
    g.begin_write().unwrap();
    g.set_property_rel(ids[0], "ts", Value::Int(1001));
    g.commit_write();
    assert_eq!(g.used_bytes(), g.accounted_bytes());

    // A ts update on a never-sealed type: no sealed entry, no sealed
    // charge.
    let lone = crate::graph::test_support::doomed_rel(&g, a, b, "V");
    g.begin_write().unwrap();
    g.set_property_rel(lone, "ts", Value::Int(7));
    g.commit_write();
    assert_eq!(g.used_bytes(), g.accounted_bytes());
}

/// A chunk the checkpoint fold fully reclaims drops its entry, and its
/// arena stream dies with it: the dead range is marked before the entry
/// goes, so compaction reclaims exactly the stream's bytes — the
/// exact-reclaim invariant ADR-0599's debug assert states, which a missing
/// mark trips (the bytes would leave the arena without ever being dead).
#[test]
fn a_fully_reclaimed_chunk_marks_its_stream_dead_before_the_entry_goes() {
    let g = Graph::default();
    let (_, arena) = crate::graph::test_support::reclaimed_folded_chunk(&g);
    let (_, dead) = g.out_side().sealed().reclaim_pair();
    assert_eq!(
        dead, arena,
        "the dead range is exactly the reclaimed stream's length"
    );

    // Compaction reclaims exactly the dead ranges — the debug assert inside
    // states the equality this pins from outside.
    g.out_side().sealed().compact();
    assert_eq!(g.out_side().sealed().reclaim_pair(), (0, 0));
}

/// A wiped arena drops its dead ranges with everything else — a clear
/// that kept them would leave the wipe charging (and holding) ranges no
/// live entry points at.
#[test]
fn a_wipe_drops_the_dead_ranges_too() {
    let g = Graph::default();
    let (_, arena) = crate::graph::test_support::reclaimed_folded_chunk(&g);
    let (_, dead) = g.out_side().sealed().reclaim_pair();
    assert_eq!(dead, arena, "the reclaimed stream is dead, not compacted");

    g.out_side().sealed().clear();
    assert_eq!(
        g.out_side().sealed().reclaim_pair(),
        (0, 0),
        "the wipe drops the arena, its entries and the dead ranges"
    );
    assert_eq!(g.out_side().sealed().floor_bytes(), 0);
    assert_eq!(g.out_side().sealed().array_bytes(), 0);
}
