# Copyright 2020 by Kurt Rathjen. All Rights Reserved.
#
# This library is free software: you can redistribute it and/or modify it 
# under the terms of the GNU Lesser General Public License as published by 
# the Free Software Foundation, either version 3 of the License, or 
# (at your option) any later version. This library is distributed in the 
# hope that it will be useful, but WITHOUT ANY WARRANTY; without even the 
# implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. 
# See the GNU Lesser General Public License for more details.
# You should have received a copy of the GNU Lesser General Public
# License along with this library. If not, see <http://www.gnu.org/licenses/>.
"""
#
# pose.py
import mutils

# Example 1:
# Save and load a pose from the selected objects
objects = maya.cmds.ls(selection=True)
mutils.savePose("/tmp/pose.json", objects)

mutils.loadPose("/tmp/pose.json")

# Example 2:
# Create a pose object from a list of object names
pose = mutils.Pose.fromObjects(objects)

# Example 3:
# Create a pose object from the selected objects
objects = maya.cmds.ls(selection=True)
pose = mutils.Pose.fromObjects(objects)

# Example 4:
# Save the pose object to disc
path = "/tmp/pose.json"
pose.save(path)

# Example 5:
# Create a pose object from disc
path = "/tmp/pose.json"
pose = mutils.Pose.fromPath(path)

# Load the pose on to the objects from file
pose.load()

# Load the pose to the selected objects
objects = maya.cmds.ls(selection=True)
pose.load(objects=objects)

# Load the pose to the specified namespaces
pose.load(namespaces=["character1", "character2"])

# Load the pose to the specified objects
pose.load(objects=["Character1:Hand_L", "Character1:Finger_L"])

"""
import logging

import mutils

try:
    import maya.cmds
except ImportError:
    import traceback
    traceback.print_exc()


__all__ = ["Pose", "savePose", "loadPose"]

logger = logging.getLogger(__name__)

_pose_ = None

# Attributes that alter a DAG node's transform.  When a pose is loaded in
# relative mode these are not applied to its anchor, which intentionally stays
# in the destination scene as the coordinate-space reference.
TRANSFORM_ATTRIBUTES = frozenset([
    "translateX", "translateY", "translateZ",
    "rotateX", "rotateY", "rotateZ",
    "scaleX", "scaleY", "scaleZ",
    "shearXY", "shearXZ", "shearYZ",
    "rotateAxisX", "rotateAxisY", "rotateAxisZ",
    "jointOrientX", "jointOrientY", "jointOrientZ",
    "rotateOrder",
])

WEAPON_CONTROL_NAMES = frozenset([
    "ctrl_r_weapon",
    "ctrl_l_weapon",
])


def savePose(path, objects, metadata=None, relativeTransform=False,
             relativeObject=None, useRootFollow=True,
             defaultRelativeObject=None):
    """
    Convenience function for saving a pose to disc for the given objects.

    Example:
        path = "C:/example.pose"
        pose = savePose(path, metadata={'description': 'Example pose'})
        print(pose.metadata())
        # {
        'user': 'Hovel', 
        'mayaVersion': '2016', 
        'description': 'Example pose'
        }

    :type path: str
    :type objects: list[str]
    :type metadata: dict or None
    :rtype: Pose
    """
    pose = mutils.Pose.fromObjects(
        objects,
        relativeTransform=relativeTransform,
    )

    if relativeTransform:
        if not objects:
            raise ValueError("Cannot save relative transforms without selected objects.")

        if useRootFollow:
            # rootFollow is normally not among the pose controls, so it is
            # deliberately used as an external anchor rather than added to
            # the saved object data.
            relativeObject = relativeObject or pose.relativeObjectInNamespace(
                objects[0], defaultRelativeObject
            )
            anchorMode = "configuredObject"
        else:
            # In manual mode the final selected control is the anchor.
            relativeObject = relativeObject or objects[-1]
            anchorMode = "selectedObject"

        pose.setRelativeTransforms(
            relativeObject,
            anchorMode=anchorMode,
            referenceName=defaultRelativeObject if useRootFollow else None,
        )

    if metadata:
        pose.updateMetadata(metadata)

    pose.save(path)

    return pose


def loadPose(path, *args, **kwargs):
    """
    Convenience function for loading the given pose path.
    
    :type path: str
    :type args: list
    :type kwargs: dict 
    :rtype: Pose 
    """
    global _pose_

    clearCache = kwargs.get("clearCache")

    if not _pose_ or _pose_.path() != path or clearCache:
        _pose_ = Pose.fromPath(path)

    _pose_.load(*args, **kwargs)

    return _pose_


class Pose(mutils.TransferObject):

    def __init__(self, relativeTransform=False):
        mutils.TransferObject.__init__(self)

        self._cache = None
        self._mtime = None
        self._cacheKey = None
        self._isLoading = False
        self._selection = None
        self._mirrorTable = None
        self._autoKeyFrame = None
        self._relativeCache = []
        self._savingRelativeTransforms = relativeTransform

    def createObjectData(self, name):
        """
        Create the object data for the given object name.
        
        :type name: str
        :rtype: dict
        """
        attrs = maya.cmds.listAttr(name, unlocked=True, keyable=True) or []
        if self._savingRelativeTransforms:
            customAttrs = set(maya.cmds.listAttr(name, userDefined=True) or [])
            attrs = [attr for attr in attrs if attr not in customAttrs]
        attrs = list(set(attrs))
        attrs = [mutils.Attribute(name, attr) for attr in attrs]

        data = {"attrs": self.attrs(name)}

        for attr in attrs:
            if attr.isValid():
                if attr.value() is None:
                    msg = "Cannot save the attribute %s with value None."
                    logger.warning(msg, attr.fullname())
                else:
                    data["attrs"][attr.attr()] = {
                        "type": attr.type(),
                        "value": attr.value()
                    }

        return data

    @staticmethod
    def relativeObjectInNamespace(objectName, relativeObjectName):
        """Return *relativeObjectName* in *objectName*'s namespace."""
        relativeObjectName = (relativeObjectName or "").strip()
        if not relativeObjectName:
            raise ValueError("No default relative object is configured.")

        # Settings store a short name.  Avoid producing a double namespace if
        # an API caller already supplied one.
        if ":" in relativeObjectName:
            return relativeObjectName

        namespace = mutils.Node(objectName).namespace()
        if namespace:
            return namespace + ":" + relativeObjectName
        return relativeObjectName

    def setRelativeTransforms(self, relativeObject, anchorMode="selectedObject",
                              referenceName=None):
        """Store every saved transform relative to *relativeObject*.

        A complete 4x4 matrix is used instead of translate/rotate values, so
        the pose remains correct when the anchor has rotation, scale, or a
        transformed parent.  The anchor may be an external rootFollow control.

        :type relativeObject: str
        :rtype: None
        """
        self.removeCustomAttributeData()

        try:
            import maya.api.OpenMaya as om
            referenceMatrix = om.MMatrix(
                maya.cmds.xform(relativeObject, query=True, worldSpace=True,
                                matrix=True)
            )
        except (RuntimeError, TypeError) as error:
            raise ValueError("Cannot read relative object matrix for %s: %s" %
                             (relativeObject, error))

        savedCount = 0
        for name, data in self.objects().items():
            try:
                objectMatrix = om.MMatrix(
                    maya.cmds.xform(name, query=True, worldSpace=True, matrix=True)
                )
            except (RuntimeError, TypeError):
                # A pose can contain non-DAG nodes.  Their normal attributes
                # are still saved; they simply cannot have a world matrix.
                continue

            data["relativeMatrix"] = list(objectMatrix * referenceMatrix.inverse())
            savedCount += 1

        if not savedCount:
            raise ValueError("No transform matrices could be saved for the pose.")

        self.setMetadata("relativeTransform", {
            "reference": relativeObject,
            "anchorMode": anchorMode,
            "referenceName": referenceName,
            "matrixConvention": "objectWorld * referenceWorld.inverse()",
        })

    def removeCustomAttributeData(self):
        """Remove user-defined attribute values from this relative pose."""
        for name, data in self.objects().items():
            customAttrs = maya.cmds.listAttr(name, userDefined=True) or []
            attrs = data.get("attrs", {})
            for attr in customAttrs:
                attrs.pop(attr, None)

    def relativeTransformData(self):
        """Return relative-transform metadata, or an empty dictionary."""
        return self.metadata().get("relativeTransform", {})

    def hasRelativeTransforms(self):
        """Return whether this pose contains relative transform matrices."""
        return bool(self.relativeTransformData().get("reference"))

    def select(self, objects=None, namespaces=None, **kwargs):
        """
        Select the objects contained in the pose file.
        
        :type objects: list[str] or None
        :type namespaces: list[str] or None
        :rtype: None
        """
        selectionSet = mutils.SelectionSet.fromPath(self.path())
        selectionSet.load(objects=objects, namespaces=namespaces, **kwargs)

    def cache(self):
        """
        Return the current cached attributes for the pose.
        
        :rtype: list[(Attribute, Attribute)]
        """
        return self._cache

    def attrs(self, name):
        """
        Return the attribute for the given name.
        
        :type name: str
        :rtype: dict
        """
        return self.object(name).get("attrs", {})

    def attr(self, name, attr):
        """
        Return the attribute data for the given name and attribute.

        :type name: str
        :type attr: str
        :rtype: dict
        """
        return self.attrs(name).get(attr, {})

    def attrType(self, name, attr):
        """
        Return the attribute type for the given name and attribute.
        
        :type name: str
        :type attr: str
        :rtype: str
        """
        return self.attr(name, attr).get("type", None)

    def attrValue(self, name, attr):
        """
        Return the attribute value for the given name and attribute.
        
        :type name: str
        :type attr: str
        :rtype: str | int | float
        """
        return self.attr(name, attr).get("value", None)

    def setMirrorAxis(self, name, mirrorAxis):
        """
        Set the mirror axis for the given name.
        
        :type name: str
        :type mirrorAxis: list[int]
        """
        if name in self.objects():
            self.object(name).setdefault("mirrorAxis", mirrorAxis)
        else:
            msg = "Object does not exist in pose. " \
                  "Cannot set mirror axis for %s"

            logger.debug(msg, name)

    def mirrorAxis(self, name):
        """
        Return the mirror axis for the given name.
        
        :rtype: list[int] | None
        """
        result = None
        if name in self.objects():
            result = self.object(name).get("mirrorAxis", None)

        if result is None:
            logger.debug("Cannot find mirror axis in pose for %s", name)

        return result

    def updateMirrorAxis(self, name, mirrorAxis):
        """
        Update the mirror axis for the given object name.
        
        :type name: str
        :type mirrorAxis: list[int]
        """
        self.setMirrorAxis(name, mirrorAxis)

    def mirrorTable(self):
        """
        Return the Mirror Table for the pose.
        
        :rtype: mutils.MirrorTable
        """
        return self._mirrorTable

    def setMirrorTable(self, mirrorTable):
        """
        Set the Mirror Table for the pose.
        
        :type mirrorTable: mutils.MirrorTable
        """
        objects = self.objects().keys()
        self._mirrorTable = mirrorTable

        for srcName, dstName, mirrorAxis in mirrorTable.matchObjects(objects):
            self.updateMirrorAxis(dstName, mirrorAxis)

    def mirrorValue(self, name, attr, mirrorAxis):
        """
        Return the mirror value for the given name, attribute and mirror axis.
        
        :type name: str
        :type attr: str
        :type mirrorAxis: list[]
        :rtype: None | int | float
        """
        value = None

        if self.mirrorTable() and name:

            value = self.attrValue(name, attr)

            if value is not None:
                value = self.mirrorTable().formatValue(attr, value, mirrorAxis)
            else:
                logger.debug("Cannot find mirror value for %s.%s", name, attr)

        return value

    def beforeLoad(self, clearSelection=True):
        """
        Called before loading the pose.
        
        :type clearSelection: bool
        """
        logger.debug('Before Load "%s"', self.path())

        if not self._isLoading:

            maya.cmds.refresh(cv=True)

            self._isLoading = True
            maya.cmds.undoInfo(openChunk=True)

            self._selection = maya.cmds.ls(selection=True) or []
            self._autoKeyFrame = maya.cmds.autoKeyframe(query=True, state=True)

            maya.cmds.autoKeyframe(edit=True, state=False)
            maya.cmds.select(clear=clearSelection)

    def afterLoad(self):
        """Called after loading the pose."""
        if not self._isLoading:
            return

        logger.debug("After Load '%s'", self.path())

        self._isLoading = False
        if self._selection:
            maya.cmds.select(self._selection)
            self._selection = None

        maya.cmds.autoKeyframe(edit=True, state=self._autoKeyFrame)
        maya.cmds.undoInfo(closeChunk=True)

        logger.debug('Loaded "%s"', self.path())

    @mutils.timing
    def load(
            self,
            objects=None,
            namespaces=None,
            attrs=None,
            blend=100,
            key=False,
            mirror=False,
            additive=False,
            refresh=False,
            batchMode=False,
            clearCache=False,
            mirrorTable=None,
            onlyConnected=False,
            clearSelection=False,
            ignoreConnected=False,
            searchAndReplace=None,
            relativeTransform=True,
    ):
        """
        Load the pose to the given objects or namespaces.
        
        :type objects: list[str]
        :type namespaces: list[str]
        :type attrs: list[str]
        :type blend: float
        :type key: bool
        :type refresh: bool
        :type mirror: bool
        :type additive: bool
        :type mirrorTable: mutils.MirrorTable
        :type batchMode: bool
        :type clearCache: bool
        :type ignoreConnected: bool
        :type onlyConnected: bool
        :type clearSelection: bool
        :type searchAndReplace: (str, str) or None
        :type relativeTransform: bool
        """
        if mirror and not mirrorTable:
            logger.warning("Cannot mirror pose without a mirror table!")
            mirror = False

        if batchMode:
            key = False

        self.updateCache(
            objects=objects,
            namespaces=namespaces,
            attrs=attrs,
            batchMode=batchMode,
            clearCache=clearCache,
            mirrorTable=mirrorTable,
            onlyConnected=onlyConnected,
            ignoreConnected=ignoreConnected,
            searchAndReplace=searchAndReplace,
            relativeTransform=relativeTransform,
        )

        self.beforeLoad(clearSelection=clearSelection)

        try:
            self.loadCache(blend=blend, key=key, mirror=mirror,
                           additive=additive)
        finally:
            if not batchMode:
                self.afterLoad()

                # Return the focus to the Maya window
                maya.cmds.setFocus("MayaWindow")
                maya.cmds.refresh(cv=True)

    def updateCache(
            self,
            objects=None,
            namespaces=None,
            attrs=None,
            ignoreConnected=False,
            onlyConnected=False,
            mirrorTable=None,
            batchMode=False,
            clearCache=True,
            searchAndReplace=None,
            relativeTransform=True,
    ):
        """
        Update the pose cache.
        
        :type objects: list[str] or None 
        :type namespaces: list[str] or None
        :type attrs: list[str] or None
        :type ignoreConnected: bool
        :type onlyConnected: bool
        :type clearCache: bool
        :type batchMode: bool
        :type mirrorTable: mutils.MirrorTable
        :type searchAndReplace: (str, str) or None
        :type relativeTransform: bool
        """
        if clearCache or not batchMode or not self._mtime:
            self._mtime = self.mtime()

        mtime = self._mtime

        cacheKey = \
            str(mtime) + \
            str(objects) + \
            str(attrs) + \
            str(namespaces) + \
            str(ignoreConnected) + \
            str(searchAndReplace) + \
            str(relativeTransform) + \
            str(maya.cmds.currentTime(query=True))

        if self._cacheKey != cacheKey or clearCache:

            self.validate(namespaces=namespaces)

            self._cache = []
            self._relativeCache = []
            self._cacheKey = cacheKey

            dstObjects = objects
            srcObjects = self.objects()
            usingNamespaces = not objects and namespaces

            if mirrorTable:
                self.setMirrorTable(mirrorTable)

            search = None
            replace = None
            if searchAndReplace:
                search = searchAndReplace[0]
                replace = searchAndReplace[1]

            matches = list(mutils.matchNames(
                srcObjects,
                dstObjects=dstObjects,
                dstNamespaces=namespaces,
                search=search,
                replace=replace,
            ))

            for srcNode, dstNode in matches:
                self.cacheNode(
                    srcNode,
                    dstNode,
                    attrs=attrs,
                    onlyConnected=onlyConnected,
                    ignoreConnected=ignoreConnected,
                    usingNamespaces=usingNamespaces,
                    relativeTransform=relativeTransform,
                )

            if relativeTransform:
                self.cacheRelativeTransforms(matches)

        if not self.cache():
            text = "No objects match when loading data. " \
                   "Turn on debug mode to see more details."

            raise mutils.NoMatchFoundError(text)

    def cacheRelativeTransforms(self, matches):
        """Build matrix application data after source and destination match."""
        data = self.relativeTransformData()
        reference = data.get("reference")
        if not reference:
            return

        # Do not infer an anchor from an incomplete selection.  Applying a
        # matrix relative to the wrong object would silently corrupt a pose.
        matched = {srcNode.name(): dstNode.name() for srcNode, dstNode in matches}
        if data.get("anchorMode") in ("rootFollow", "configuredObject"):
            # The target anchor uses the namespace of the target controls.
            # rootFollow is retained for compatibility with relative poses
            # written before the anchor name became configurable.
            referenceName = data.get("referenceName") or "ctrl_c_rootFollow"
            destinationReference = self.relativeObjectInNamespace(
                next(iter(matched.values())), referenceName
            ) if matched else None
        else:
            destinationReference = matched.get(reference)

        if destinationReference and not maya.cmds.objExists(destinationReference):
            destinationReference = None
        if not destinationReference:
            logger.warning(
                "Skipping relative transforms: cannot find destination reference "
                "for saved object '%s'.",
                reference,
            )
            return

        for sourceName, destinationName in matched.items():
            matrix = self.object(sourceName).get("relativeMatrix")
            if not isinstance(matrix, (list, tuple)) or len(matrix) != 16:
                continue
            self._relativeCache.append((destinationName, matrix,
                                        destinationReference))

    def cacheNode(
            self,
            srcNode,
            dstNode,
            attrs=None,
            ignoreConnected=None,
            onlyConnected=None,
            usingNamespaces=None,
            relativeTransform=False,
    ):
        """
        Cache the given pair of nodes.
        
        :type srcNode: mutils.Node
        :type dstNode: mutils.Node
        :type attrs: list[str] or None 
        :type ignoreConnected: bool or None
        :type onlyConnected: bool or None
        :type usingNamespaces: none or list[str]
        :type relativeTransform: bool
        """
        mirrorAxis = None
        mirrorObject = None

        # Remove the first pipe in-case the object has a parent
        dstNode.stripFirstPipe()

        srcName = srcNode.name()
        relativeReference = self.relativeTransformData().get("reference")

        if self.mirrorTable():
            mirrorObject = self.mirrorTable().mirrorObject(srcName)

            if not mirrorObject:
                mirrorObject = srcName
                msg = "Cannot find mirror object in pose for %s"
                logger.debug(msg, srcName)

            # Check if a mirror axis exists for the mirrorObject otherwise
            # check the srcNode
            mirrorAxis = self.mirrorAxis(mirrorObject) or self.mirrorAxis(srcName)

            if mirrorObject and not maya.cmds.objExists(mirrorObject):
                msg = "Mirror object does not exist in the scene %s"
                logger.debug(msg, mirrorObject)

        if usingNamespaces:
            # Try and use the short name.
            # Much faster than the long name when setting attributes.
            try:
                dstNode = dstNode.toShortName()
            except mutils.NoObjectFoundError as msg:
                logger.debug(msg)
                return
            except mutils.MoreThanOneObjectFoundError as msg:
                logger.debug(msg)

        customAttrs = set()
        if relativeTransform:
            customAttrs = set(
                maya.cmds.listAttr(dstNode.name(), userDefined=True) or []
            )

        for attr in self.attrs(srcName):

            # The anchor supplies the destination coordinate space.  Its
            # transform must remain at its current scene position; otherwise
            # loading its saved attributes would defeat relative placement.
            if relativeTransform and srcName == relativeReference and \
                    attr in TRANSFORM_ATTRIBUTES:
                continue

            # A relative pose contains placement data; it must never alter
            # animator-defined attributes, including in older files which may
            # have saved them before this rule was introduced.
            if relativeTransform and attr in customAttrs:
                continue

            if attrs and attr not in attrs:
                continue

            dstAttribute = mutils.Attribute(dstNode.name(), attr)
            isConnected = dstAttribute.isConnected()

            if (ignoreConnected and isConnected) or (onlyConnected and not isConnected):
                continue

            type_ = self.attrType(srcName, attr)
            value = self.attrValue(srcName, attr)
            srcMirrorValue = self.mirrorValue(mirrorObject, attr, mirrorAxis=mirrorAxis)

            srcAttribute = mutils.Attribute(dstNode.name(), attr, value=value, type=type_)
            dstAttribute.update()

            self._cache.append((srcAttribute, dstAttribute, srcMirrorValue))

    def loadCache(self, blend=100, key=False, mirror=False, additive=False):
        """
        Load the pose from the current cache.
        
        :type blend: float
        :type key: bool
        :type mirror: bool
        :rtype: None
        """
        cache = self.cache()

        for i in range(0, len(cache)):
            srcAttribute, dstAttribute, srcMirrorValue = cache[i]
            if srcAttribute and dstAttribute:
                if mirror and srcMirrorValue is not None:
                    value = srcMirrorValue
                else:
                    value = srcAttribute.value()
                try:
                    dstAttribute.set(value, blend=blend, key=key,
                                     additive=additive)
                except (ValueError, RuntimeError):
                    cache[i] = (None, None)
                    logger.debug('Ignoring %s', dstAttribute.fullname())

        self.loadRelativeTransforms(blend=blend, key=key, mirror=mirror,
                                    additive=additive)

    def loadRelativeTransforms(self, blend=100, key=False, mirror=False,
                               additive=False):
        """Apply cached relative matrices in world space.

        Matrix loading deliberately applies only at 100%.  Blending, additive
        poses, and mirroring have existing attribute-specific semantics and
        cannot safely be represented by a single world-space matrix.
        """
        if not self._relativeCache:
            return
        if mirror or additive or blend != 100:
            logger.debug(
                "Skipping relative transforms for mirror, additive, or blended pose load."
            )
            return

        import maya.api.OpenMaya as om

        # Weapon controls can depend on the transforms set by the rest of the
        # rig.  Keep their saved order relative to each other, but always
        # apply both after every other relative transform.
        relativeCache = sorted(
            self._relativeCache,
            key=lambda entry: mutils.Node(entry[0]).shortname().split(":")[-1]
            in WEAPON_CONTROL_NAMES,
        )

        for destinationName, relativeMatrix, referenceName in relativeCache:
            try:
                referenceMatrix = om.MMatrix(
                    maya.cmds.xform(referenceName, query=True, worldSpace=True,
                                    matrix=True)
                )
                worldMatrix = om.MMatrix(relativeMatrix) * referenceMatrix
                maya.cmds.xform(destinationName, worldSpace=True,
                                matrix=list(worldMatrix))
                if key:
                    maya.cmds.setKeyframe(destinationName, attribute=[
                        "translateX", "translateY", "translateZ",
                        "rotateX", "rotateY", "rotateZ",
                        "scaleX", "scaleY", "scaleZ",
                    ])
            except (RuntimeError, TypeError, ValueError) as error:
                logger.debug("Ignoring relative transform for %s: %s",
                             destinationName, error)
