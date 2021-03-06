# standard library
from functools import partial
import logging
import abc
logger = logging.getLogger(__name__)

# maya library
import pymel.core

# local library
import nodex.utils

VERBOSE = False


class UndefinedNodexError(TypeError):
    """ Error that is raised when the Nodex can't be defined with a relevant datatype to the passed in data. """
    pass


class Nodex(object):
    """ Abstract class that is base for all Nodex datatype classes.

    The names of nodes and attributes can be passed to this class, and the appropriate subclass will be determined.
    Also you can pass in a value based on certain datatypes to allow for automatic conversion (eg. Matrices)
    The conversion are based on definitions defined in `nodex.datatypes`

    If the value can't be converted to a valid data type an error will be raised.
    This behaviour is similar to Pymel's implementation of the `pymel.core.PyNode`
    """
    _priority = 999999

    @classmethod
    def priority(cls):
        """
        :return: The priority for lookup/check against data whether it should become this datatype. Lower is earlier.
        """
        return cls._priority

    def __new__(cls, *args, **kwargs):
        """ Catch all creation for Nodex classes, creates correct class depending on type passed. """
        data = None
        dt = kwargs.get("type", None)

        if not args:
            # Assume default for datatype
            # Create default value for type if no args provided, but type has been provided
            if issubclass(cls, Nodex):
                data = cls.default()
            elif dt is not None:
                data = dt()     # instantiate by type
        else:
            data = args[0]
            if len(args) > 1:
                # Assume attribute passes as two args: ( node, attr )
                # Let's use PyMel to do the possible conversion and checks for us and use the resulting Attribute
                data = pymel.core.PyNode(*args)

        if data is None:
            raise TypeError("A Nodex cannot be instantiated with None data. Data received: {0}".format(data))
        assert data is not None

        if isinstance(data, Nodex):
            return data

        # We shouldn't make this assumption here, plus it breaks a lot of stuff. :)
        #if isinstance(data, (list, tuple)) and len(data) == 1:
        #    data = data[0]

        newcls = None
        if cls is not Nodex:
            # A Nodex class was explicitly required, if data was passed to init check whether it is compatible with
            # the required class. If no existing object was passed, create of the required class Nodex with default
            # values
            if not cls.isValidData(data):
                raise TypeError("Given data {0} is not compatible with datatype {1}".format(data, cls.__name__))
            newcls = cls
        else:
            newcls = _getDataTypeFromData(data, dt)

        if newcls:
            self = super(Nodex, cls).__new__(newcls)
            self.setReference(data, validate=False)
            return self
        else:
            raise UndefinedNodexError("Could not determine Nodex datatype for {0}.".format(data))

    @staticmethod
    def isValidData(data):
        return False

    def asAttribute(self):
        """ Creates a node that holds the reference data's value as a constant within an Attribute and returns the
            connectable Attribute as a Nodex. """
        raise NotImplementedError()

    def setReference(self, data, validate=True):
        self._dimensions = None     # remove cached dimensions
        if validate:
            if not self.isValidData(data):
                raise TypeError("Can't set data to this datatype.")
        self._data = self.convertData(data)

    @abc.abstractmethod
    def convertData(self, data):
        """
            The returned type must be something that can be validly used as a value again for convertData, plus
            should be settable to a PyMel attribute (that relates to the datatype)
        """
        pass

    @staticmethod
    def default():
        """
        :return: Default value for this datatype. The returned type must be something that can be validly converted by
                 this datatype in 'self.convertData'
        """
        raise NotImplementedError()

    def value(self):
        data = self._data
        if self.isSingleAttribute():
            return data.get()
        elif isinstance(data, tuple):
            return tuple(x.get() if isinstance(x, pymel.core.Attribute) else x.value() for x in data)
        else:
            return data

    # region nodex combined methods (whilst referencing: attribute || single numeric || array)
    def dimensions(self):
        if self._dimensions is not None:
            return self._dimensions

        if self.isSingleAttribute():
            self._dimensions = nodex.utils.attrDimensions(self.attr())
            return self._dimensions
        elif self.isAttribute():
            # Since it's not a single numeric we know it's a tuple (see `self.isAttribute()`)
            return len(self._data)
        elif self.isSingleNumeric():
            return 1
        else:
            return len(self._data)

    def isSingleNumeric(self):
        if self.isSingleAttribute():
            return nodex.utils.attrDimensions(self.attr()) == 1
        else:
            return isinstance(self._data, (int, float, bool))
    # endregion

    # region nodex attribute methods
    def attr(self):
        """ Returns the attribute this Nodex instance is referencing.
            If not referencing an attribute an error is raised

            :rtype: pymel.core.Attribute or tuple(pymel.core.Attribute)
        """
        if self.isAttribute():
            return self._data
        else:
            raise AttributeError("Data referenced by this nodex is not a `pymel.core.Attribute`")

    def isSingleAttribute(self):
        """ Returns True if this instance references a single valid attribute.

            If this returns True then the output of self.attr() is accesible `pymel.core.Attribute`)

            If this returns False but `isAttribute()` is True, then the output of self.attr() is a tuple of
            `pymel.core.Attribute`
        """
        if isinstance(self._data, pymel.core.Attribute):
            return True

    def isAttribute(self):
        """ Returns True if this Nodex instance references a valid attribute, else False. """
        if isinstance(self._data, tuple) and (all(isinstance(x, pymel.core.Attribute) or
                                                  (isinstance(x, Nodex) and x.isAttribute())) for x in self._data):
            return True
        elif self.isSingleAttribute():
            return True

        return False

    def node(self):
        """ Returns the node for the attribute that this Nodex instance is referencing.
            If not referencing an attribute an error is raised """
        if self.isAttribute():
            return self._data.node()
        else:
            raise AttributeError("This nodex does not reference an Attribute so does not refer to a node."
                                 "Data: {0}".format(self._data))
    # endregion

    def clearValue(self):
        """ Set the default value for this instance (reset value). """
        defaultValue = self.default()
        if defaultValue is None:
            raise RuntimeError("Can't clear the value for: {0}".format(self))
        self.setReference(defaultValue)

    def connect(self, other, allowGrow=True, clearLarger=False):
        """
            Connects one Nodex attribute/value to another attribute.
            This method ensures to perform a connection even if the dimensions between the Nodex attributes differs.

            self (source) ---> other (destination)

            :param other: The destination Nodex to connect to.
            :type other: Nodex

            :return: The resulting dimensions
        """
        if not isinstance(other, Nodex):
            other = Nodex(other)

        dim = self.dimensions()
        otherDim = other.dimensions()

        if not other.isAttribute():
            raise ValueError('Can\'t connect to a Nodex that does not reference an Attribute. '
                             'Other is: {0}'.format(other))

        if dim == otherDim:
            if self.isSingleAttribute():
                self.attr().connect(other.attr()) # connect pymel attributes
            elif self.isAttribute():
                # non-single Attribute
                for i, x in enumerate(self._data):
                    x.connect(other[i].attr())
            else:
                if other.isSingleAttribute():
                    other.attr().set(self.value())  # assign referenced value
                else:
                    if dim == 1 and otherDim == 1:  # the magical one-tuple array issue
                        other[0].attr().set(self.value())  # assign referenced value
                    else:
                        values = self.value()
                        for i, other_element in enumerate(other):
                            other_element.attr().set(values[i])  # assign referenced value
            return dim
        elif dim == 1 and allowGrow:   # --> otherDim != 1 and otherDim > 1
            for i in range(otherDim):
                self.connect(other[i])
            logger.debug('Connected single attribute {0} to larger attribute {1}. '
                           'Attribute connected to all inputs of larger attribute'.format(
                           self, other))
            return otherDim
        elif otherDim < dim:
            self[:otherDim].connect(other)
            logger.debug('Truncated attribute {0} to connect to smaller attribute {1}.'.format(
                            self, other))
            return otherDim
        elif otherDim > dim:
            resultDim = self.connect(other[:dim])  # connect to truncated length of other
            logger.warning('Connected smaller attibute {0} to larger attibute {1}.'.format(self, other))
            if clearLarger:
                other[dim+1:].clearValue()   # clear remaining values
                logger.debug('clearLarger parameter is True, remaining values are cleared.')
            return resultDim
    # endregion

    def __getitem__(self, item):
        """ Retrieve an item or slice of this Nodex.

            You can use this to access part of a datatype that has more than 1 dimension.
            It also directly allows you to access children attributes of a compound/array attribute.
        """
        if self.isSingleAttribute():
            attr = self.attr()
            if attr.isArray():
                if isinstance(item, int):
                    return Nodex(attr.elementByPhysicalIndex(item))
                elif isinstance(item, slice):
                    return Nodex([attr.elementByPhysicalIndex(i) for i in xrange(item.start, item.stop, item.step)])
            elif attr.isCompound():
                return Nodex(attr.children()[item])

        if isinstance(item, slice):
            return Nodex(self._data[item])
        elif isinstance(item, int):
            return Nodex(self._data[item])

    def __len__(self):
        """ Returns the dimensions of this Nodex """
        return self.dimensions()

    def __iter__(self):
        """ Iterate over the elements in this Nodex """
        for x in xrange(len(self)):
            yield self[x]

    def __str__(self):
        return "{0}({1})".format(self.__class__.__name__, self._data)

    def __repr__(self):
        return "{0}({1})".format(self.__class__.__name__, self._data)

    # region override right hand operators
    def __radd__(self, other):
        """ Gereneric implementation for right hand + operator """
        return Nodex(other) + self

    def __rsub__(self, other):
        """ Gereneric implementation for right hand - operator """
        return Nodex(other) - self

    def __rmul__(self, other):
        """ Gereneric implementation for right hand * operator """
        return Nodex(other) * self

    def __rdiv__(self, other):
        """ Gereneric implementation for right hand / operator """
        return Nodex(other) / self

    # def __rmod__(self, other):
    #     return Nodex(other) % self

    def __rpow__(self, other):
        """ Gereneric implementation for right hand pow() operator """
        return Nodex(other ^ self)

    def __rxor__(self, other):
        """ Gereneric implementation for right hand ^ operator """
        return Nodex(other ^ self)
    # endregion



# TODO: (Define behaviour) Implement Nodex.insertInput() so we can easily pass-through a graph of nodes like Pymel
# TODO: (Define behaviour) Implement method to allow quick insert of any of the mathematical operations on an attribute.
#       Thus basically grabbing the current outputs for an output and passing them through the newly created node.
#       Nodex("pSphere1.translateX").mergeInto(Math.sum, with_others) or something along those lines
#       This would be rather similar behaviour like Pymel.core.Attribute.insertInput() but for outputs.
#       (Since Nodex' could behave that way it would be a nice unique feature)


class Math(object):
    """
        The Math class holds many staticmethods for generic mathematical functionality that can operate on a Nodex.
    """
    @staticmethod
    def bimath(self, other, func):
        """ Convenience method for the special methods like __add__, __sub__, etc. """
        if not isinstance(other, Nodex):
            other = Nodex(other)

        return func(self, other)

    sum = partial(nodex.utils.plusMinusAverage, operation=1, name="sum", dimensions=None)
    multiply = partial(nodex.utils.multiplyDivide, operation=1, name="multiply")
    multDouble = partial(nodex.utils.doubleLinear, nodeType="multDoubleLinear", name="multDouble")
    divide = partial(nodex.utils.multiplyDivide, operation=2, name="divide")
    power = partial(nodex.utils.multiplyDivide, operation=3, name="power")
    add = partial(nodex.utils.doubleLinear, nodeType="addDoubleLinear", name="add")
    sum = partial(nodex.utils.plusMinusAverage, dimensions=None, operation=1, name="sum")
    sum1D = partial(nodex.utils.plusMinusAverage, dimensions=1, operation=1, name="sum1D")
    sum2D = partial(nodex.utils.plusMinusAverage, dimensions=2, operation=1, name="sum2D")
    sum3D = partial(nodex.utils.plusMinusAverage, dimensions=3, operation=1, name="sum3D")
    subtract = partial(nodex.utils.plusMinusAverage, dimensions=None, operation=2, name="subtract")
    subtract1D = partial(nodex.utils.plusMinusAverage, dimensions=1, operation=2, name="subtract1D")
    subtract2D = partial(nodex.utils.plusMinusAverage, dimensions=2, operation=2, name="subtract2D")
    subtract3D = partial(nodex.utils.plusMinusAverage, dimensions=3, operation=2, name="subtract3D")
    average1D = partial(nodex.utils.plusMinusAverage, dimensions=1, operation=3, name="average1D")
    average2D = partial(nodex.utils.plusMinusAverage, dimensions=2, operation=3, name="average2D")
    average3D = partial(nodex.utils.plusMinusAverage, dimensions=3, operation=3, name="average3D")
    clamp = partial(nodex.utils.clamp, name="clamp")
    equal = partial(nodex.utils.condition, operation=0, name="equal")
    notEqual = partial(nodex.utils.condition, operation=1, name="notEqual")
    greaterThan = partial(nodex.utils.condition, operation=2, name="greaterThan")
    greaterOrEqual = partial(nodex.utils.condition, operation=3, name="greaterOrEqual")
    lessThan = partial(nodex.utils.condition, operation=4, name="lessThan")
    lessOrEqual = partial(nodex.utils.condition, operation=5, name="lessOrEqual")

    @staticmethod
    def sqrt(nodex, name='sqrt'):
        """ Return the square root of the given nodex """
        sqrt_result = Math.power(nodex, 0.5, name=name)
        sqrt_result.node().attr("input2").lock()    # lock this attribute to be safe
        return sqrt_result

    @staticmethod
    def abs(nodex, name="abs", dimensions=None):
        """ Return the absolute value of the given nodex """
        pow_result = Math.power(nodex, 2.0, name="{0}_pow".format(name))
        pow_result.node().attr("input2").lock()     # lock this attribute to be safe
        sqrt_result = Math.power(pow_result, 0.5, name="{0}_sqrt".format(name))
        sqrt_result.node().attr("input2").lock()    # lock this attribute to be safe
        return sqrt_result

    @staticmethod
    def blend(input1, input2, blender=None, name='blend'):
        """ Returns the blended values

            Uses `blendColors` node.

            .. warning:: Not implemented yet.
        """
        # TODO: Implement Math.blend
        raise NotImplementedError()

    @staticmethod
    def setRange(value, min, max=None, oldMin=None, oldMax=None, name='setRange'):
        """ Returns the blended values

            Uses `setRange` node.

            .. warning:: Not implemented yet.
        """
        # TODO: Implement Math.blend
        raise NotImplementedError()

    @staticmethod
    def lookupTable(fn, minInput, maxInput, steps=200):
        """ Returns the output of an animCurve node where the curve has been set to the provided function calculated
            at `steps` amount between minInput and maxInput.

            This could be used to implement non-default algorithms/nodes into Maya; especially those with a repetitive
            pattern like sin/cos/tan.

            .. warning:: Not implemented yet.

        """
        # TODO: Implement Math.lookupTable
        raise NotImplementedError()

    @staticmethod
    def overlay(input1, input2):
        """
            Implementation of overlay

            Source: http://en.wikipedia.org/wiki/Blend_modes#Overlay
        """
        # TODO: Test overlay implementation
        # TODO: Rewrite overlay implementation so node names are more relevant
        a = Nodex(input1)
        b = Nodex(input2)
        fn1 = 2*a*b
        fn2 = 1 - 2 * (1-a) * (1-b)
        overlay = Math.lessThan(a, 0.5, ifTrue=fn1, ifFalse=fn2)
        return overlay

    @staticmethod
    def contrast(value, contrast=None, bias=None):
        """ Returns the value modified by contrast from bias.

            Uses `contrast` node.

            .. warning:: Not implemented yet.

        """
        # TODO: Implement Math.contrast
        raise NotImplementedError()


# TODO: Math.reverse
# TODO: Math.stencil
# TODO: Math.unitConversion


# Cache the nodex datatypes in sorted order
def find_subclasses(module, clazz):
    import inspect
    return [cls for name, cls in inspect.getmembers(module) if cls != clazz and
                                                               inspect.isclass(cls) and
                                                               issubclass(cls, clazz)]


def find_nodex_subclasses_sorted():
    import nodex.datatypes
    kls = find_subclasses(nodex.datatypes, Nodex)
    return list(sorted(kls, key=lambda x: x.priority()))


def _getDataTypeFromData(data, datatype=None, cache=[]):
    """
        Secretly caches the sorted list in cache upon first run. :O
    """
    #TODO: Implement more pythonic/safer caching method, instead of doing the quick 'n' dirty route.
    if datatype is not None:
        if not issubclass(datatype, Nodex):
            raise TypeError("Preferred datatype should be of type Nodex")

        if datatype.isValidData(data):
            return datatype

    if not cache:
        cache[:] = find_nodex_subclasses_sorted()

    for cls in cache:
        if VERBOSE:
            logger.debug("Checking data {0} against {1}".format(data, cls.__name__))
        if cls.isValidData(data):
            if VERBOSE:
                logger.debug("Matched data {0} with {0}".format(data, cls.__name__))
            return cls