"""Sea arrival policy shared by exports, vessel checks and container views.

The captured source is never changed. Road/rail movements cannot supply a sea ETA.
"""
import re

def _name(value):
    if isinstance(value,dict):return str(value.get('name') or value.get('label') or value.get('title') or '')
    return str(value or '')

def is_inland_leg(leg):
    names=[_name(leg.get(k)).strip().upper() for k in ('vessel','vessel_name','vesselName')]
    modes=[_name(leg.get(k)).strip().upper() for k in ('transport_mode','mode_of_transport','transport_type','mode')]
    return any(re.match(r'^(?:TRUCK(?:ING)?|TRAILER|LORRY|ROAD|RAIL|TRAIN|LAND TRANSPORT)(?:\b|[_/\-])',v) for v in names+modes if v)

def sea_legs(schedule):
    """Return copies of sea legs with original leg numbering, never renumber routes."""
    legs=next((schedule[k] for k in ('legs','vessel_legs','schedule_legs','vessel_schedule_legs','transshipments') if isinstance(schedule.get(k),list) and schedule[k]),None)
    if legs is None:
        legs=[schedule] if any(schedule.get(k) for k in ('vessel','vessel_name','vesselName')) else []
    result=[]
    for index,leg in enumerate(legs,1):
        if isinstance(leg,dict) and not is_inland_leg(leg):
            result.append(dict(leg,leg_number=leg.get('leg_number') or leg.get('leg_no') or index))
    return result
