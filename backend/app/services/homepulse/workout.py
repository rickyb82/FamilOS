"""Pure workout scheduling and explicitly estimated energy calculations.

MET classifications: https://pacompendium.com/conditioning-exercise/
The conventional oxygen-based conversion used here is
MET * 3.5 mL O2/kg/min * body mass / 200 = estimated kcal/min.
It is a population-level approximation, not calorimetry or a heart-rate model.
Net activity energy subtracts one conventional MET, not a measured resting rate.

Cunningham (1980): https://pubmed.ncbi.nlm.nih.gov/7435418/
500 + 22 * lean kg estimates basal kcal/day. It is reported separately and is
never used to invent a body-composition correction for exercise calories.
"""
import math

MAX_TOTAL_SECONDS = 24 * 60 * 60


def _number(value, label, lower, upper, *, optional=False, integer=False):
    if value is None and optional:
        return None
    valid_type = type(value) is int if integer else type(value) in (int, float)
    if not valid_type or not lower <= value <= upper or not math.isfinite(value):
        raise ValueError(f'{label}: valore non valido ({lower}–{upper})')
    return value if integer else float(value)


def validate_plan(plan):
    """Return a normalized independent list of up to 40 user-defined phases.

    Targets and METs are supplied by the user; no intensity is prescribed here.
    Empty plans retain the simple work/rest timer.
    """
    if not isinstance(plan, list) or len(plan) > 40:
        raise ValueError('La scheda deve contenere al massimo 40 fasi')
    normalized = []
    for index, phase in enumerate(plan):
        if not isinstance(phase, dict):
            raise ValueError(f'Fase {index + 1}: formato non valido')
        name = phase.get('name')
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
            raise ValueError(f'Fase {index + 1}: nome richiesto, massimo 80 caratteri')
        duration = _number(phase.get('duration'), 'Durata fase', 5, 7200, integer=True)
        target_min = _number(phase.get('target_min'), 'FC minima', 40, 240, optional=True, integer=True)
        target_max = _number(phase.get('target_max'), 'FC massima', 40, 240, optional=True, integer=True)
        if (target_min is None) != (target_max is None):
            raise ValueError('Inserisci entrambi gli estremi dell’intervallo cardiaco, oppure nessuno')
        if target_min is not None and target_min >= target_max:
            raise ValueError('La FC minima della fase deve essere inferiore alla FC massima')
        met = _number(phase.get('met'), 'MET della fase', 1, 20, optional=True)
        normalized.append({'name': name.strip(), 'duration': duration,
                           'target_min': target_min, 'target_max': target_max, 'met': met})
    if sum(phase['duration'] for phase in normalized) > MAX_TOTAL_SECONDS:
        raise ValueError('La durata della scheda non può superare 24 ore')
    return normalized


def validate_profile(profile):
    """Normalize optional adult profile data; unknown fields are not retained."""
    if not isinstance(profile, dict):
        raise ValueError('Profilo non valido')
    values = {}
    for key, label, low, high in (
        ('weight_kg', 'Peso in kg', 30, 350),
        ('body_fat_pct', 'Massa grassa percentuale', 2, 65),
        ('height_cm', 'Altezza in cm', 100, 250),
        ('age', 'Età', 18, 100),
        ('reported_lean_pct', 'Massa magra dichiarata percentuale', 2, 98),
        ('reported_retention_pct', 'Ritenzione dichiarata percentuale', 0, 100),
    ):
        values[key] = _number(profile.get(key), label, low, high, optional=True,
                              integer=key == 'age')
    for key, label in (('goal', 'Obiettivo'), ('composition_source', 'Fonte della composizione')):
        value = profile.get(key)
        if value is not None and (not isinstance(value, str) or len(value.strip()) > 200):
            raise ValueError(f'{label}: massimo 200 caratteri')
        values[key] = value.strip() if isinstance(value, str) and value.strip() else None
    return values


def composition(profile):
    """Return derived composition and a separate Cunningham basal estimate.

    No lean/fat masses are inferred without both total weight and fat percent.
    Fat-free mass is used as the operational lean-mass estimate here; it is not
    a direct muscle-mass measurement. The method of the supplied fat estimate
    determines much of its uncertainty.
    """
    profile = validate_profile(profile)
    weight, fat = profile['weight_kg'], profile['body_fat_pct']
    reported = profile['reported_lean_pct']
    fat_kg = weight * fat / 100 if weight is not None and fat is not None else None
    reported_lean_kg = weight * reported / 100 if weight is not None and reported is not None else None
    result = {'fat_kg': fat_kg, 'lean_kg': None, 'reported_lean_kg': reported_lean_kg,
              'basal_kcal_day': None, 'composition_status': 'Dati insufficienti'}
    # Water is part of fat-free mass, not a third independent compartment to add
    # to fat and lean percentages. Keep incompatible labels exactly as reported.
    if reported is not None and fat is not None and abs(reported + fat - 100) > 1:
        result['composition_status'] = 'Da chiarire: massa magra e grassa non complementari'
        return result
    if fat_kg is None:
        return result
    lean_kg = weight - fat_kg
    result.update(lean_kg=lean_kg, basal_kcal_day=500 + 22 * lean_kg,
                  composition_status='Stimata dal peso e dalla massa grassa')
    return result


def _schedule_parts(work, rest, rounds, plan):
    work = _number(work, 'Lavoro', 5, 7200, integer=True)
    rest = _number(rest, 'Recupero', 0, 7200, integer=True)
    rounds = _number(rounds, 'Ripetizioni', 1, 100, integer=True)
    phases = validate_plan(plan if plan is not None else [])
    if not phases:
        phases = [{'name': 'Lavoro', 'duration': work, 'target_min': None,
                   'target_max': None, 'met': None}]
        if rest:
            phases.append({'name': 'Recupero', 'duration': rest, 'target_min': None,
                           'target_max': None, 'met': None})
    cycle = sum(phase['duration'] for phase in phases)
    total = cycle * rounds
    if total > MAX_TOTAL_SECONDS:
        raise ValueError('La sessione completa, incluse le ripetizioni, non può superare 24 ore')
    return phases, cycle, rounds, total


def schedule(elapsed, work, rest, rounds, plan=None):
    """Resolve an elapsed session time to its user-defined phase and target.

    Exact boundaries enter the next phase/cycle; zero recovery creates no
    phantom phase. Completion clears targets/MET so it cannot guide more work.
    """
    elapsed = _number(elapsed, 'Tempo trascorso', 0, 10**12)
    phases, cycle, rounds, total = _schedule_parts(work, rest, rounds, plan)
    if elapsed >= total:
        return {'phase': 'Completato', 'round': rounds, 'remaining': 0,
                'total': total, 'step_index': len(phases) - 1,
                'target_min': None, 'target_max': None, 'met': None}
    round_index = int(elapsed // cycle)
    position = elapsed - round_index * cycle
    for index, phase in enumerate(phases):
        if position < phase['duration']:
            return {'phase': phase['name'], 'round': round_index + 1,
                    'remaining': math.ceil(phase['duration'] - position),
                    'total': total, 'step_index': index,
                    'target_min': phase['target_min'], 'target_max': phase['target_max'],
                    'met': phase['met']}
        position -= phase['duration']
    raise ValueError('Tempo di fase non risolvibile')


def energy_for_interval(start_elapsed, end_elapsed, config, profile):
    """Integrate estimated kcal across phase/cycle boundaries without sampling.

    Returns gross_kcal, net_kcal, estimated_seconds and unestimated_seconds.
    Missing weight or phase MET contributes only unestimated_seconds; no calorie
    estimate is manufactured from heart rate or from a missing activity type.
    Integrals are clipped to session bounds. Complexity is O(number of phases),
    even for many cycles, and complete sessions are bounded to 24 hours.
    """
    start = _number(start_elapsed, 'Inizio intervallo', 0, 10**12)
    end = _number(end_elapsed, 'Fine intervallo', 0, 10**12)
    if end < start:
        raise ValueError('La fine dell’intervallo precede l’inizio')
    if not isinstance(config, dict):
        raise ValueError('Configurazione della sessione non valida')
    phases, cycle, rounds, total = _schedule_parts(
        config.get('work'), config.get('rest'), config.get('rounds'), config.get('plan'))
    profile = validate_profile(profile)
    weight = profile['weight_kg']
    start, end = min(start, total), min(end, total)
    if weight is None:
        return {'gross_kcal': 0.0, 'net_kcal': 0.0,
                'estimated_seconds': 0.0, 'unestimated_seconds': end - start}

    # Rates are per second; /60 converts the conventional kcal/min formula.
    rates = []
    for phase in phases:
        met = phase['met']
        rates.append((0.0, 0.0, 0.0, 1.0) if met is None else
                     (met * 3.5 * weight / 200 / 60,
                      (met - 1) * 3.5 * weight / 200 / 60, 1.0, 0.0))
    cycle_integral = [sum(phase['duration'] * rate[i]
                          for phase, rate in zip(phases, rates)) for i in range(4)]

    def integral(at):
        complete_cycles = min(int(at // cycle), rounds)
        remainder = at - complete_cycles * cycle
        values = [complete_cycles * value for value in cycle_integral]
        for phase, rate in zip(phases, rates):
            duration = min(remainder, phase['duration'])
            if duration <= 0:
                break
            for i in range(4):
                values[i] += duration * rate[i]
            remainder -= duration
        return values

    before, after = integral(start), integral(end)
    return dict(zip(('gross_kcal', 'net_kcal', 'estimated_seconds', 'unestimated_seconds'),
                    (max(0.0, after[i] - before[i]) for i in range(4))))
