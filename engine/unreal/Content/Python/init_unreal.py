"""Expose project operations through Epic's local Unreal MCP server.

Import, saving and playback are separate explicit tools. They deliberately do
not run from a Slate startup tick: saving assets can re-enter editor ticks.
"""
import dynacity_tools
from toolset_registry.registration import Registration

_registration=Registration([dynacity_tools.DynaCityTools])
_registration.register()

import point_cloud_layer
point_cloud_layer.register_menu()

import explore_city
explore_city.register_menu()

import city_tools
city_tools.register()

import driving_tools
driving_tools.register()

import street_tools
street_tools.register()
import street_context
street_context.register_menu()

import location_tools
location_tools.register()

import control_panel
control_panel.register()

import mobility_tools
mobility_tools.register()

import overture_context
overture_context.register()

import captured_buildings
captured_buildings.register()
