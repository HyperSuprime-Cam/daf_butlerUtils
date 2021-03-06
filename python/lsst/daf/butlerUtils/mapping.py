#!/bin/env python
# 
# LSST Data Management System
# Copyright 2008, 2009, 2010 LSST Corporation.
# 
# This product includes software developed by the
# LSST Project (http://www.lsst.org/).
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.    See the
# GNU General Public License for more details.
# 
# You should have received a copy of the LSST License Statement and 
# the GNU General Public License along with this program.  If not, 
# see <http://www.lsstcorp.org/LegalNotices/>.
#

import os
import re
from lsst.daf.persistence import ButlerLocation
import lsst.pex.policy as pexPolicy

"""This module defines the Mapping base class."""

class Mapping(object):

    """Mapping is a base class for all mappings.  Mappings are used by
    the Mapper to map (determine a path to some data given some
    identifiers) and standardize (convert data into some standard
    format or type) data, and to query the associated registry to see
    what data is available.

    Subclasses must specify self.storage or else override self.map().

    Public methods: lookup, have, need, getKeys, map

    Mappings are specified mainly by policy.  A Mapping policy should
    consist of:

    template (string): a Python string providing the filename for that
    particular dataset type based on some data identifiers.  In the
    case of redundancy in the path (e.g., file uniquely specified by
    the exposure number, but filter in the path), the
    redundant/dependent identifiers can be looked up in the registry.

    python (string): the Python type for the retrieved data (e.g.
    lsst.afw.image.ExposureF)

    persistable (string): the Persistable registration for the on-disk data
    (e.g. ImageU)

    storage (string, optional): Storage type for this dataset type (e.g.
    "BoostStorage")

    level (string, optional): the level in the camera hierarchy at which the
    data is stored (Amp, Ccd or skyTile), if relevant

    tables (string, optional): a whitespace-delimited list of tables in the
    registry that can be NATURAL JOIN-ed to look up additional
    information.  """

    def __init__(self, datasetType, policy, registry, root, provided=None):
        """Constructor for Mapping class.
        @param datasetType    (string)
        @param policy         (lsst.pex.policy.Policy) Mapping policy
        @param registry       (lsst.daf.butlerUtils.Registry) Registry for metadata lookups
        @param root           (string) Path of root directory
        @param provided       (list of strings) Keys provided by the mapper
        """

        if policy is None:
            raise RuntimeError, "No policy provided for mapping"

        self.datasetType = datasetType
        self.registry = registry
        self.root = root

        self.template = policy.getString("template") # Template path
        self.keyDict = dict([
            (k, _formatMap(v, k, datasetType))
            for k, v in
            re.findall(r'\%\((\w+)\).*?([diouxXeEfFgGcrs])', self.template)
            ])
        if provided is not None:
            for p in provided:
                if p in self.keyDict:
                    del self.keyDict[p]
        self.python = policy.getString("python") # Python type
        self.persistable = policy.getString("persistable") # Persistable type
        self.storage = policy.getString("storage")
        if policy.exists("level"):
            self.level = policy.getString("level") # Level in camera hierarchy
        if policy.exists("tables"):
            self.tables = policy.getStringArray("tables")
        else:
            self.tables = None
        self.range = None
        self.columns = None
        self.obsTimeName = policy.getString("obsTimeName") \
                if policy.exists("obsTimeName") else None

    def keys(self):
        """Return the dict of keys and value types required for this mapping."""
        return self.keyDict

    def map(self, mapper, dataId, write=False):
        """Standard implementation of map function.
        @param mapper (lsst.daf.persistence.Mapper)
        @param dataId (dict) Dataset identifier
        @return (lsst.daf.persistence.ButlerLocation)"""

        actualId = self.need(self.keyDict.iterkeys(), dataId)
        path = mapper._mapActualToPath(self.template, actualId)
        if not os.path.isabs(path):
            path = os.path.join(self.root, path)
        if not write:
            newPath = mapper._parentSearch(path)
            if newPath is not None:
                path = newPath

        addFunc = "add_" + self.datasetType # Name of method for additionalData
        if hasattr(mapper, addFunc):
            addFunc = getattr(mapper, addFunc)
            additionalData = addFunc(actualId)
            assert isinstance(additionalData, dict), "Bad type for returned data"
        else:
            additionalData = actualId.copy()
        
        return ButlerLocation(self.python, self.persistable, self.storage, path, additionalData)

    def lookup(self, properties, dataId):
        """Look up properties for in a metadata registry given a partial
        dataset identifier.
        @param properties (list of strings)
        @param dataId     (dict) Dataset identifier
        @return (list of tuples) values of properties"""

        if self.registry is None:
            raise RuntimeError, "No registry for lookup"

        where = []
        values = []
        fastPath = True
        for p in properties:
            if p not in ('filter', 'expTime', 'taiObs'):
                fastPath = False
                break
        if fastPath and dataId.has_key('visit') and "raw" in self.tables:
            return self.registry.executeQuery(properties, ('raw_visit',),
                    [('visit', '?')], None, (dataId['visit'],))
        if dataId is not None:
            for k, v in dataId.iteritems():
                if self.columns and not k in self.columns:
                    continue
                if k in ("tract", "patch"):  # never try to query on coadd-related things
                    continue
                if k == self.obsTimeName:
                    continue
                where.append((k, '?'))
                values.append(v)
        if self.range is not None:
            values.append(dataId[self.obsTimeName])
        return self.registry.executeQuery(properties, self.tables,
                where, self.range, values)

    def have(self, properties, dataId):
        """Returns whether the provided data identifier has all
        the properties in the provided list.
        @param properties (list of strings) Properties required
        @parm dataId      (dict) Dataset identifier
        @return (bool) True if all properties are present"""
        for prop in properties:
            if not dataId.has_key(prop):
                return False
        return True

    def need(self, properties, dataId):
        """Ensures all properties in the provided list are present in
        the data identifier, looking them up as needed.  This is only
        possible for the case where the data identifies a single
        exposure.
        @param properties (list of strings) Properties required
        @param dataId     (dict) Partial dataset identifier
        @return (dict) copy of dataset identifier with enhanced values
        """

        newId = dataId.copy()
        newProps = []                    # Properties we don't already have
        for prop in properties:
            if not newId.has_key(prop):
                newProps.append(prop)
        if len(newProps) == 0:
            return newId

        lookups = self.lookup(newProps, newId)
        if len(lookups) != 1:
            raise RuntimeError, "No unique lookup for %s from %s: %d matches" % (newProps, newId, len(lookups))
        for i, prop in enumerate(newProps):
            newId[prop] = lookups[0][i]
        return newId

def _formatMap(ch, k, datasetType):
    """Convert a format character into a Python type."""
    if ch in "diouxX":
        return int
    elif ch in "eEfFgG":
        return float
    elif ch in "crs":
        return str
    else:
        raise RuntimeError("Unexpected format specifier %s"
                " for field %s in template for dataset %s" %
                (ch, k, datasetType))


class ImageMapping(Mapping):
    """ImageMapping is a Mapping subclass for non-camera images."""

    def __init__(self, datasetType, policy, registry, root, **kwargs):
        """Constructor for Mapping class.
        @param datasetType    (string)
        @param policy         (lsst.pex.policy.Policy) Mapping policy
        @param registry       (lsst.daf.butlerUtils.Registry) Registry for metadata lookups
        @param root           (string) Path of root directory"""
        Mapping.__init__(self, datasetType, policy, registry, root, **kwargs)
        self.columns = policy.getStringArray("columns") if policy.exists("columns") else None


class ExposureMapping(Mapping):
    """ExposureMapping is a Mapping subclass for normal exposures."""

    def __init__(self, datasetType, policy, registry, root, **kwargs):
        """Constructor for Mapping class.
        @param datasetType    (string)
        @param policy         (lsst.pex.policy.Policy) Mapping policy
        @param registry       (lsst.daf.butlerUtils.Registry) Registry for metadata lookups
        @param root           (string) Path of root directory"""
        Mapping.__init__(self, datasetType, policy, registry, root, **kwargs)
        self.columns = policy.getStringArray("columns") if policy.exists("columns") else None

    def standardize(self, mapper, item, dataId):
        return mapper._standardizeExposure(self, item, dataId)

class CalibrationMapping(Mapping):
    """CalibrationMapping is a Mapping subclass for calibration-type products.

    The difference is that data properties in the query or template
    can be looked up using a reference Mapping in addition to this one.

    CalibrationMapping Policies can contain the following:

    reference (string, optional): a list of tables for finding missing dataset
    identifier components (including the observation time, if a validity range
    is required) in the exposure registry; note that the "tables" entry refers
    to the calibration registry

    refCols (string, optional): a list of dataset properties required from the
    reference tables for lookups in the calibration registry

    validRange (bool): true if the calibration dataset has a validity range
    specified by a column in the tables of the reference dataset in the
    exposure registry) and two columns in the tables of this calibration
    dataset in the calibration registry)
    
    obsTimeName (string, optional): the name of the column in the reference
    dataset tables containing the observation time (default "taiObs")
    
    validStartName (string, optional): the name of the column in the
    calibration dataset tables containing the start of the validity range
    (default "validStart")

    validEndName (string, optional): the name of the column in the
    calibration dataset tables containing the end of the validity range
    (default "validEnd") """

    def __init__(self, datasetType, policy, registry, calibRegistry, calibRoot, **kwargs):
        """Constructor for Mapping class.
        @param datasetType    (string)
        @param policy         (lsst.pex.policy.Policy) Mapping policy
        @param registry       (lsst.daf.butlerUtils.Registry) Registry for metadata lookups
        @param calibRegistry  (lsst.daf.butlerUtils.Registry) Registry for calibration metadata lookups
        @param calibRoot      (string) Path of calibration root directory"""

        Mapping.__init__(self, datasetType, policy, calibRegistry, calibRoot, **kwargs)
        self.reference = policy.getStringArray("reference") \
                if policy.exists("reference") else None
        self.refCols = policy.getStringArray("refCols") \
                if policy.exists("refCols") else None
        self.refRegistry = registry
        if policy.exists("validRange") and policy.getBool("validRange"):
            self.range = ("?", policy.getString("validStartName"),
                policy.getString("validEndName"))
        if policy.exists("columns"):
            self.columns = policy.getStringArray("columns")
        if policy.exists("filter"):
            self.setFilter = policy.getBool("filter")
            
    def lookup(self, properties, dataId):
        """Look up properties for in a metadata registry given a partial
        dataset identifier.
        @param properties (list of strings)
        @param dataId     (dict) Dataset identifier
        @return (list of tuples) values of properties"""

# Either look up taiObs in reference and then all in calibRegistry
# Or look up all in registry

        newId = dataId.copy()
        if self.reference is not None:
            where = []
            values = []
            for k, v in dataId.iteritems():
                if self.refCols and k not in self.refCols:
                    continue
                where.append((k, '?'))
                values.append(v)

            # Columns we need from the regular registry
            if self.columns is not None:
                columns = set(self.columns)
                for k in dataId.iterkeys():
                    columns.discard(k)
            else:
                columns = set(properties)

            if not columns:
                # Nothing to lookup in reference registry; continue with calib
                # registry
                return Mapping.lookup(self, properties, newId)

            lookups = self.refRegistry.executeQuery(columns, self.reference,
                    where, None, values)
            if len(lookups) != 1:
                raise RuntimeError("No unique lookup for %s from %s: %d matches" %
                                   (columns, dataId, len(lookups)))
            if columns == set(properties):
                # Have everything we need
                return lookups
            for i, prop in enumerate(columns):
                newId[prop] = lookups[0][i]
        return Mapping.lookup(self, properties, newId)

    def standardize(self, mapper, item, dataId):
        return mapper._standardizeExposure(self, item, dataId, filter=self.setFilter)

class DatasetMapping(Mapping):
    """DatasetMapping is a Mapping subclass for non-Exposure datasets that can
    be retrieved by the standard daf_persistence mechanism.

    The differences are that the Storage type must be specified and no
    Exposure standardization is performed.

    The "storage" entry in the Policy is mandatory; the "tables" entry is
    optional; no "level" entry is allowed.  """

    def __init__(self, datasetType, policy, registry, root, **kwargs):
        """Constructor for DatasetMapping class.
        @param[in,out] mapper (lsst.daf.persistence.Mapper) Mapper object
        @param policy         (lsst.pex.policy.Policy) Mapping policy
        @param datasetType    (string)
        @param registry       (lsst.daf.butlerUtils.Registry) Registry for metadata lookups
        @param root           (string) Path of root directory"""
        Mapping.__init__(self, datasetType, policy, registry, root, **kwargs)
        self.storage = policy.getString("storage") # Storage type
