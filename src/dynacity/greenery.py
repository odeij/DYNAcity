"""Shared OSM greenery classification; retain original tags alongside display categories."""
NATURAL = {
    'tree':'tree','tree_row':'tree_row','tree_stump':'tree_stump','shrub':'shrub','bush':'shrub',
    'wood':'wood','scrub':'scrub','shrubbery':'shrubbery','heath':'heath',
    'grassland':'grass','meadow':'grass','fell':'heath','tundra':'heath','moor':'heath',
    'wetland':'wetland','reedbed':'reedbed','moss':'moss','lichen':'moss',
}
LANDUSE = {
    'forest':'wood','grass':'grass','meadow':'grass','recreation_ground':'grass',
    'orchard':'orchard','vineyard':'vineyard','plant_nursery':'plant_nursery',
    'allotments':'allotments','farmland':'farmland','flowerbed':'flowerbed',
    'greenery':'greenery','shrubbery':'shrubbery','village_green':'village_green',
    'greenfield':'greenfield','greenhouse_horticulture':'greenhouse_horticulture',
    'plantation':'orchard','cemetery':'cemetery',
}
LANDCOVER = {'trees':'wood','tree':'wood','wood':'wood','forest':'wood',
             'grass':'grass','grasses':'grass','meadow':'grass','shrubs':'shrubbery',
             'scrub':'scrub','bushes':'shrubbery','shrubbery':'shrubbery',
             'flowerbed':'flowerbed','flowers':'flowerbed','greenery':'greenery',
             'moss':'moss','reeds':'reedbed','reedbed':'reedbed','heath':'heath'}
AREA_KINDS = set(NATURAL.values()) | set(LANDUSE.values()) | set(LANDCOVER.values()) | {
    'park','garden','planter','green_roof','grass_paver'}
AREA_KINDS -= {'tree','tree_row','tree_stump','shrub','hedge'}
POINT_KINDS = {'tree','shrub','tree_stump','planter','hedge'}


def vegetation_kind(tags):
    if tags.get('natural') in NATURAL: return NATURAL[tags['natural']]
    if tags.get('barrier') in {'hedge','hedge_bank'}: return 'hedge'
    if tags.get('man_made') == 'planter': return 'planter'
    if tags.get('landcover') in LANDCOVER: return LANDCOVER[tags['landcover']]
    if tags.get('landuse') in LANDUSE: return LANDUSE[tags['landuse']]
    if tags.get('leisure') in {'park','garden','nature_reserve'}:
        return tags['leisure'] if tags['leisure'] != 'nature_reserve' else 'greenery'
    if tags.get('roof:material') in {'grass','vegetation','green_roof'} or tags.get('green_roof') == 'yes':
        return 'green_roof'
    if tags.get('garden:type') or tags.get('garden:style'): return 'garden'
    if tags.get('surface') in {'grass','grass_paver'} and not tags.get('highway'):
        return tags['surface']
    return None


def greenery_inventory(osm):
    botanical = {'species','genus','taxon','leaf_type','leaf_cycle','vegetation'}
    return [{'id':f"{e['type']}/{e['id']}", 'kind':vegetation_kind(e.get('tags',{})),
             'tags':e.get('tags',{}), 'as_of':e.get('source_as_of'),
             'retrieved_on':e.get('retrieved_on',osm.get('retrieved_on'))}
            for e in osm.get('elements',[]) if vegetation_kind(e.get('tags',{})) or
            botanical.intersection(e.get('tags',{}))]
