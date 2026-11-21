//! `config/_merge.py`: `deep_merge`.

use sylvia_base::pyyaml::{data_key_eq, Data};

/// `deep_merge(base, override)`: a new dict where the override's keys win, dicts merge
/// recursively, lists and scalars replace, and `None` removes the key. Both are dicts.
pub fn deep_merge(base: &[(Data, Data)], over: &[(Data, Data)]) -> Vec<(Data, Data)> {
    let mut result: Vec<(Data, Data)> = base.to_vec();
    for (key, value) in over {
        let position = result.iter().position(|(existing, _)| data_key_eq(existing, key));
        match (value, position) {
            (Data::None, Some(index)) => {
                result.remove(index);
            }
            (Data::None, None) => {}
            (Data::Dict(pairs), Some(index)) if matches!(result[index].1, Data::Dict(_)) => {
                let Data::Dict(existing) = &result[index].1 else { unreachable!() };
                result[index].1 = Data::Dict(deep_merge(existing, pairs));
            }
            (value, Some(index)) => result[index].1 = value.clone(),
            (value, None) => result.push((key.clone(), value.clone())),
        }
    }
    result
}
