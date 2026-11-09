"""Regional extract -> company store: real pbf file, nothing from the network."""

import pytest

osmium = pytest.importorskip("osmium")

from leadly.devtools.import_osm import native_path, read_file, split_by_niche  # noqa: E402

KAZAN = [55.6, 48.8, 55.9, 49.4]


def write_extract(path):
    writer = osmium.SimpleWriter(f"{native_path(path.parent)}/{path.name}")
    writer.add_node(osmium.osm.mutable.Node(id=1, location=(49.1, 55.7), tags={
        "shop": "beauty", "name": "Лотос", "phone": "+7 843 111-22-33"}))
    writer.add_node(osmium.osm.mutable.Node(id=2, location=(49.2, 55.8), tags={"shop": "beauty", "name": "Без контактов"}))
    writer.add_node(osmium.osm.mutable.Node(id=3, location=(49.2, 55.8), tags={"shop": "beauty"}))  # no name
    writer.add_node(osmium.osm.mutable.Node(id=4, location=(49.2, 55.8), tags={"shop": "bakery", "name": "Булочная"}))
    writer.add_node(osmium.osm.mutable.Node(id=5, location=(37.6, 55.7), tags={"amenity": "dentist", "name": "Москва"}))
    writer.add_node(osmium.osm.mutable.Node(id=6, location=(49.15, 55.75)))  # corner of the building below
    writer.add_node(osmium.osm.mutable.Node(id=7, location=(49.16, 55.76)))
    writer.add_way(osmium.osm.mutable.Way(id=10, nodes=[6, 7], tags={"amenity": "dentist", "name": "Клиника"}))
    writer.close()


def test_reads_named_businesses_of_our_niches_with_positions(tmp_path):
    path = tmp_path / "region.osm.pbf"
    write_extract(path)
    elements = read_file(path)
    assert {e["tags"]["name"] for e in elements} == {"Лотос", "Без контактов", "Москва", "Клиника"}
    building = next(e for e in elements if e["type"] == "way")
    assert round(building["center"]["lat"], 2) == 55.75


def test_only_the_city_goes_in_and_contacts_come_first(tmp_path):
    path = tmp_path / "region.osm.pbf"
    write_extract(path)
    niches = split_by_niche(read_file(path), KAZAN)
    assert [e["tags"]["name"] for e in niches["beauty"]] == ["Лотос", "Без контактов"]
    assert [e["tags"]["name"] for e in niches["dental"]] == ["Клиника"]  # the Moscow clinic stays out
