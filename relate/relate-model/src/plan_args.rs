//! The plan wire args' scalar readers (ADR-0345): the optional-int /
//! bool / fraction extraction policies and the all-or-none, non-negative
//! group read every metric family shares. Their own module below both
//! the node accessor (`plan_wire`) and the fused-child parser
//! (`plan_fused`), so the dependency runs one way — both read here,
//! neither reads the other.

use crate::CypherValue;

/// The optional-integer arg — one extraction policy for every optional
/// metric key: absent or an integer, a malformed value fails loudly.
pub(super) fn optional_int(args: &crate::IndexMap<String, CypherValue>, key: &str) -> Option<i64> {
    match args.get(key) {
        None => None,
        Some(CypherValue::Int(n)) => Some(*n),
        other => panic!("plan wire {key} is an integer when present (ADR-0345), got {other:?}"),
    }
}

/// The optional-boolean arg — the same policy as [`optional_int`] for the
/// one boolean fact (LABS-369's walk direction).
pub(super) fn optional_bool(
    args: &crate::IndexMap<String, CypherValue>,
    key: &str,
) -> Option<bool> {
    match args.get(key) {
        None => None,
        Some(CypherValue::Bool(flag)) => Some(*flag),
        other => panic!("plan wire {key} is a boolean when present (LABS-369), got {other:?}"),
    }
}

/// The optional-fraction arg — [`optional_int`]'s policy for the one
/// float metric (the frontier budget fraction, ADR-0516): absent or a
/// non-negative float, a malformed value fails loudly.
pub(super) fn optional_fraction(
    args: &crate::IndexMap<String, CypherValue>,
    key: &str,
) -> Option<f64> {
    match args.get(key) {
        None => None,
        Some(CypherValue::Float(fraction)) if *fraction >= 0.0 => Some(*fraction),
        other => {
            panic!("plan wire {key} is a non-negative float when present (ADR-0516), got {other:?}")
        }
    }
}

/// The rides-the-metric-set group policy (one loud spelling, never a
/// hand-rolled copy per field family): the keys' values are an
/// all-or-none group of non-negative integers, present only when the
/// metric set is — a partial group, a group without metrics, or a
/// negative value is a wire-contract break kept loud. `None` when the
/// whole group is absent (an EXPLAIN node).
pub(super) fn metric_set_ints(
    args: &crate::IndexMap<String, CypherValue>,
    keys: &[&str],
    has_metrics: bool,
) -> Option<Vec<i64>> {
    match (has_metrics, metric_group_ints(args, keys)) {
        (false, None) => None,
        (true, Some(values)) => Some(values),
        (false, Some(_)) => panic!(
            "plan wire {keys:?} are present without the metric fields \
             (all-or-none group policy)"
        ),
        (true, None) => panic!(
            "plan wire {keys:?} are absent from the metric fields \
             (all-or-none group policy)"
        ),
    }
}

/// The all-or-none, non-negative group read — `metric_set_ints` minus its
/// metric-set gate, so the scalar metric block, the riding families (read
/// kinds, phase times) and the fused seek child's measured pair share one
/// extraction policy. A partial group or a negative value is a
/// wire-contract break kept loud; `None` when every key is absent.
pub(super) fn metric_group_ints(
    args: &crate::IndexMap<String, CypherValue>,
    keys: &[&str],
) -> Option<Vec<i64>> {
    let values: Vec<Option<i64>> = keys.iter().map(|k| optional_int(args, k)).collect();
    let any_present = values.iter().any(Option::is_some);
    let all_present = values.iter().all(Option::is_some);
    match (any_present, all_present) {
        (false, _) => None,
        (true, false) => panic!(
            "plan wire {keys:?} are an all-or-none group, got a partial group: \
             values={values:?}"
        ),
        (true, true) => {
            if let Some((key, value)) = keys
                .iter()
                .zip(&values)
                .find_map(|(key, value)| value.filter(|n| *n < 0).map(|n| (*key, n)))
            {
                panic!(
                    "plan wire {key} is non-negative when present, got {value} \
                     (all-or-none non-negative group)"
                );
            }
            Some(
                values
                    .into_iter()
                    .map(|v| v.expect("the group is all-present"))
                    .collect(),
            )
        }
    }
}
