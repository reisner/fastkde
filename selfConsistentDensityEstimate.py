#!/usr/bin/env python
from numpy import *
import numpy as npy
from numpy.random import randn
from scipy.optimize import newton
import empiricalCharacteristicFunction as ecf
#If numpy's version is less than 1.7, then use the version of arraypad
#supplied with this code, since pad() doesn't exist in lower numpy versions
if(float(".".join(__version__.split(".")[:2])) < 1.7):
  from arraypad import pad
import copy
from types import *
import pdb
import time
import sys
from nufft import calcTfromX
import floodFillSearch as flood

#A simple timer for comparing ECF calculation methods
class Timer():
   def __init__(self,n=None): self.n = n
   def __enter__(self): self.start = time.time()
   def __exit__(self, *args): print "N = {}, t = {} seconds".format(self.n,time.time() - self.start)

def nextHighestPowerOfTwo(number):
    """Returns the nearest power of two that is greater than or equal to number"""
    return int(2**(ceil(log2(number))))

class selfConsistentDensityEstimate:

  def __init__( self,\
                data = None,\
                axes = None, \
                numPointsPerSigma = 10, \
                numPoints=None, \
                doApproximateECF = True, \
                ecfPrecision = 1, \
                doSaveTransformedKernel = False, \
                doFFT = True, \
                doSaveMarginals = True, \
                beVerbose = False, \
                fracContiguousHyperVolumes = 1, \
                numContiguousHyperVolumes = None, \
                positiveShift = False, \
                countThreshold = None, \
              ):
    """ 

    Estimates the density function of a given dataset using the self-consistent
    method of Bernacchia and Pigolotti (2011, J. R. Statistic Soc. B.).  Prior
    to estimating the PDF, the data are standardized to have a mean of 0 and a
    variance of 1.  
    
    Standardization is done so that PDFs of varying widths can be calculated on
    a unified grid; the original PDF can be re-obtained by scaling, offsetting,
    and renormalizing the calculated PDF.  Assuming the PDF is reasonably
    narrow, then most of the information in the PDF should be contained in the
    returned domain.  The width of the domain is set in terms of multiples of
    unit standard deviations of the data; the default is 20-sigma.

    input:
    ------

      data (array_like)   : the data from which to estimate the PDF.  Should be 1-
                            or 2-dimensional. If 2-dimensional, this flags calculation
                            of an N-dimensional PDF.  The first index
                            should refer to each variable and the second index the
                            observations of the varibles.

      axes                : the axis-values of the estimated PDF.  They must be evenly
                            spaced and they should have a length that is a power of two
                            plus one (e.g., 33).

      numPointsPerSigma   : the number of points on the data grid per standard
                            deviation; this influences the total size of the axes that are
                            automatically calculated if no other aspects of the grid are specified.

      numPoints           : the number of points to use for the pdf grid. If provided as a scalar,
                            each axis will have the same number of points.  Otherwise, it should be an
                            iterable with a value for each axis length.  Axis lengths should be a power
                            of two plus one (e.g., 33)

      deltaX              : if given, this specifies the spacing between domain
                            values.

      doApproximateECF    : flags whether to approximate the ECF using a (much faster)
                            FFT.  In tests, this is accurate to ~1e-14 over low 
                            frequencies, but is inaccurate to ~1e-2 for the highest ~5% 
                            of frequencies.

      ecfPrecision        : sets the precision of the approximate ECF.  If set to 2, it uses
                            double precision accuracy; 1 otherwise

      doFFT               : flags whether to calculate phiSC and its FFT to obtain
                            pdf

      doSaveMarginals     : flags whether to calculate and save the marginal distributions

      fracContiguousHyperVolumes :  the fraction of contiguous hypervolumes of the ECF, that 
                                    are above the ECF threshold, to use in the density estimate

      numContiguousHyperVolumes : like fracContiguousHyperVolumes, but specify an integer number
                                  to use.  fracContiguousHyperVolumes will be ignored if this
                                  is provided as an argument.

      positiveShift     : translate the PDF vertically such that the estimate is positive or
                          0 everywhere

      countThreshold    : this argument does nothing; it has been deprecated.  It is kept as an argument for backward
                          compatibility.

    Returns: a selfConsistentDensityEstimate object

    """

    def vprint(msg):
        """Only print if beVerbose is True"""
        if beVerbose:
            print(msg)

    addOne = True #Force x grids to be (2**n) + 1
    
    if(data is not None):

      #Save the original data for the marginal calculation
      originalData = array(data)

      #First check the rank of the data
      dataRank = len(shape(data))
      #If the data are a vector, promote the data to a rank-1 array with only 1 column
      if(dataRank == 1):
          data = array(originalData[newaxis,:],dtype=npy.float)
      else:
          data = array(originalData,dtype=npy.float)
      if(dataRank > 2):
          raise ValueError,"data must be a rank-2 array of shape [numVariables,numDataPoints]"

      #Set the rank of the data
      self.dataRank = dataRank

      #Set the number of variables
      self.numVariables = shape(data)[0]
      #Set the number of data points
      self.numDataPoints = shape(data)[1]

      self.fracContiguousHyperVolumes = fracContiguousHyperVolumes

      if numContiguousHyperVolumes is not None:
          self.fracContiguousHyperVolumes = numContiguousHyperVolumes

      vprint("Operating on data with numVariables = {}, numDataPoints = {}".format(self.numVariables,self.numDataPoints))

    else:
      self.numDataPoints = 0

    #Store the doFFT flag
    self.doFFT = doFFT

    #Save the marginals flag
    self.doSaveMarginals = doSaveMarginals
    if self.numVariables == 1:
        self.doSaveMarginals = False

    #Set whether to approximate the ECF using the FFT method
    self.doApproximateECF = doApproximateECF

    #Set the approximate ECF precision
    self.ecfPrecision = ecfPrecision

    #Preinitialize the ecf threshold
    self.ecfThreshold = None

    #Flag whether to save the transformed kernel
    self.doSaveTransformedKernel = doSaveTransformedKernel
    #initialize the kernel and its transform
    self.kappaSC = None
    self.kSC = None

    self.positiveShift = positiveShift

    #***********************
    # Calculate the x grids
    #***********************
    if(axes is None):

        #Get the range of the data 
        self.xMin = amin(data,1)
        self.xMax = amax(data,1)

        vprint("Data stats:")
        vprint("\tminima: {}".format(self.xMin))
        vprint("\tmaxima: {}".format(self.xMax))

        #Get the grid mid-points
        midPoint = 0.5*(self.xMax + self.xMin)

        #inflate the range by 5% to ensure that the data all fit within the range
        self.xMin += 0.05*(self.xMin-midPoint)
        self.xMax += 0.05*(self.xMax-midPoint)

        if numPoints is None:
            #Calculate the number of standard deviations there
            # are in the data range
            dataRange = self.xMax - self.xMin
            numSigma = dataRange/std(data,axis=1)
            
            #Set the number of points for each dimensions
            self.numXPoints = array([nextHighestPowerOfTwo(ns * numPointsPerSigma) + int(addOne) for ns in numSigma])
        else:
            #If we can iterate through 
            try:
                lenNum = len(numPoints)
                isIterable = True
            except:
                isIterable = False
                lenNum = 1

            if isIterable:
                if lenNum == self.numVariables:
                    self.numXPoints = numPoints
                else:
                    raise ValueError,"len(numPoints) = {}, but it should match numVariables = {}".format(lenNum,self.numVariables)
            else:
                self.numXPoints = array(self.numVariables*(numPoints,))


        #Set the grids for each dimension
        self.axes = [ linspace(xmin,xmax,np) for xmin,xmax,np in zip(self.xMin,self.xMax,self.numXPoints)]

        vprint("Grids created with xmin: {}, xmax: {}, npoints: {}".format(self.xMin,self.xMax,self.numXPoints))
    else:
        #Set the xgrid from the function argument
        self.axes = axes
        self.xMin = array([amin(xg) for xg in axes])
        self.xMax = array([amax(xg) for xg in axes])
        self.numXPoints = array([len(xg) for xg in axes])
        #Get the grid mid-points
        self.midPoint = 0.5*(self.xMax + self.xMin)


    #Set the midpoint of the incoming grid
    self.dataMid = 0.5*(self.xMax + self.xMin)
    #Set the range to be +/- pi
    self.dataNorm = (self.xMax - self.xMin)/pi

    #Get the grid spacings
    self.deltaX = array([ xg[1] - xg[0] for xg in self.axes])

    #Save xgrids as axes for backward compatibility
    self.xgrids = self.axes

    #Check that the axes are regular and proper powers of two
    for v in range(self.numVariables):
        xg = self.axes[v]
        dx = (xg[1:]-self.dataMid[v])/self.dataNorm[v] - (xg[:-1] - self.dataMid[v])/self.dataNorm[v]
        dxdiff = dx - self.deltaX[v]/self.dataNorm[v]
        fTolerance = self.deltaX[v]/(1e4*self.dataNorm[v])
        #Check that these differences are less than 1/1e6
        if(not all(abs(dxdiff) < fTolerance)):
            raise ValueError,"All grids in axes must be regularly spaced"

        log2size = log2(len(xg) - addOne)
        if log2size != floor(log2size):
            if addOne:
                extraStr = " + 1"
            else:
                extraStr = ""

            raise ValueError,"All grids in axes must be powers of 2" + extraStr + ", but got {}".format(len(xg))

    #Calculate the frequency point grids (for 0-centered data)
    self.tgrids = [ calcTfromX((xg-av)/sd) for xg,av,sd in zip(self.axes,self.dataMid,self.dataNorm) ]
    self.numTPoints = array([len(tg) for tg in self.tgrids])
    self.deltaT = array([tg[2] - tg[1] for tg in self.tgrids])

    self.phiSC = (0.0+0.0j)*zeros(self.numTPoints)
    self.ECF = (0.0+0.0j)*zeros(self.numTPoints)

    #Initialize the good distribution index
    self.goodDistributionInds = []

    #Set the verbosity flag
    self.beVerbose = beVerbose

    self.convolvedData = None

    #Initialize the marginals
    self.marginalObjects = None

    if(data is not None):

      #*************************************************
      # Calculate the Empirical Characteristic Function
      #*************************************************
      #Note that this routine also standardizes the data on-the-fly
      vprint("Calculating the ECF")
      sys.stdout.flush()

      #Transfrom the data to 0-centered coordinates
      for v in range(self.numVariables):
          data[v,:] = (data[v,:] - self.dataMid[v])/self.dataNorm[v]
          
      #Calculate the ECF (see empiricalCharacteristicFunction.py)
      ecfObj = ecf.ECF( inputData = data, \
                        tgrids = self.tgrids, \
                        useFFTApproximation = self.doApproximateECF, \
                        precision = self.ecfPrecision, \
                        beVerbose = self.beVerbose)

      #Extract the ECF from the ECF object
      self.ECF = ecfObj.ECF

      if(self.doFFT):
        #*************************************************
        # Apply the filter
        #*************************************************
        #Apply the Bernacchia and Pigolotti (2011) filter to the ECF to obtain
        #the fourier representation of the self-consistent density
        vprint("Applying the filter")
        self.applyBernacchiaFilter()

        #*************************************************
        # Transform to real space
        #*************************************************
        #Transform the optimal distribution to real space
        vprint("Transforming to real space")
        sys.stdout.flush()
        self.__transformphiSC__()

        #Calculate and save the marginal distribution objects
        if(self.doSaveMarginals):
          self.marginalObjects = []
          for i in xrange(self.numVariables):
            self.marginalObjects.append(selfConsistentDensityEstimate(originalData[i,:], \
                                          axes = [self.axes[i]], \
                                          positiveShift = self.positiveShift, \
                                          fracContiguousHyperVolumes = self.fracContiguousHyperVolumes, \
                                          doSaveMarginals = False) )
                                                                  
    return


  #*****************************************************************************
  #** selfConsistentDensityEstimate: ***********************************************
  #******************* applyBernacchiaFilter() *********************************
  #*****************************************************************************
  #*****************************************************************************
  def applyBernacchiaFilter(self,doFlushArrays=True):
    """ Given an ECF, calculate the self-consistent density in fourier-space by
    applying the BP11 filter."""

    #Make an easy-to-read and float version of self.numDataPoints
    N = float(self.numDataPoints)

    #Calculate the stability threshold for the ECF
    ecfThresh = 4.*(N-1.)/(N*N)
    self.ecfThreshold = ecfThresh

    #Calculate the squared magnitude of the ECF 
    ecfSq = abs(self.ECF)**2

    #Find all hypervolumes where ecfSq is greater than the stability threshold
    contiguousInds = flood.floodFillSearch(ecfSq,searchThreshold = self.ecfThreshold)

    if contiguousInds == []:
        raise RuntimeError,"No ECF values found above the ECF threshold.  max(ecfSq) = {}, ecfThresh = {}".format(amax(ecfSq),ecfThresh)

    #Sort them by distance from the center
    sortedInds = flood.sortByDistanceFromCenter(contiguousInds,shape(ecfSq))

    numVolumes = len(sortedInds)
    if self.fracContiguousHyperVolumes >= 1:
        numVolumesToUse = int(self.fracContiguousHyperVolumes)
    else:
        numVolumesToUse = int(self.fracContiguousHyperVolumes*numVolumes)
    if numVolumesToUse < 1:
        numVolumesToUse = 1

    #Initialize the filtered value list
    iCalcPhi = self.numVariables*[array([],dtype='int')]

    #Pull out fracContiguousHyperVolumes of contiguous hyper volumes, in order of distance from
    #the origin
    for i in xrange(numVolumesToUse):
        for n in xrange(self.numVariables):
            iCalcPhi[n] = concatenate( (iCalcPhi[n],sortedInds[i][n]) )

    #Convert iCalcPhi to a list of tuples, such that it is compatible with the output of where()
    if self.numVariables != 1:
      iCalcPhi = [ tuple(ii) for ii in iCalcPhi ]

    #Save the filter
    self.iCalcPhi = iCalcPhi
   
    #If flagged, clear the phiSC array.  This is needed if the same selfConsistentDensityEstimate object
    #is reused for multiple data.
    if(doFlushArrays):
      self.phiSC[:] = (0.0+0.0j)

    #Calculate the transform of the self-consistent Kernel (and only calculate it at
    # points where ecfSq is above ecfThresh)
    kappaSC = (1.0+0.0j)*zeros(shape(self.ECF))
    kappaSC[iCalcPhi] = (N/(2*(N-1)))\
                              *(1+sqrt(1-ecfThresh/ecfSq[iCalcPhi]))

    #Store the fourier kernel if we are going to save the transformed kernel
    if(self.doSaveTransformedKernel):
      self.kappaSC = kappaSC

    midPointAccessor = tuple([(tp-1)/2 for tp in self.numTPoints])
    #Calculate the transform of the self-consistent density estimate
    self.phiSC[iCalcPhi] = self.ECF[iCalcPhi]*kappaSC[iCalcPhi]

    if(self.beVerbose):
        print("Normalization of kappaSC, ECF, and phiSC: {}, {}, {}".format(kappaSC[midPointAccessor],self.ECF[midPointAccessor],self.phiSC[midPointAccessor]))

  #*****************************************************************************
  #** selfConsistentDensityEstimate: ***********************************************
  #******************* findGoodDistributionInds() ******************************
  #*****************************************************************************
  #*****************************************************************************
  def findGoodDistributionInds(self):
    """Find indices of the optimal distribution that are above 0.0"""
    return where(self.pdf >= 0.0)

  #*****************************************************************************
  #** selfConsistentDensityEstimate: ***********************************************
  #******************* findBadDistributionInds() *******************************
  #*****************************************************************************
  #*****************************************************************************
  def findBadDistributionInds(self):
    """Find indices of the optimal distribution that are below 0.0"""
    return where(self.pdf < 0.0)

  #*****************************************************************************
  #** selfConsistentDensityEstimate: ***********************************************
  #******************* __transformphiSC__() ************************************
  #*****************************************************************************
  #*****************************************************************************
  def __transformphiSC__(self):
    """ Transform the self-consistent estimate of the distribution from
    frequency space to real space"""

    #Transform the PDF estimate to real space
    pdf = fft.fftshift(real(fft.fftn(fft.ifftshift(self.phiSC))))*prod(self.deltaT)*(1./(2*pi))**self.numVariables

    #Unnormalize it
    pdf /= prod(self.dataNorm)
    
    #transpose the self-consistent density estimate
    self.pdf = pdf.transpose()

    #Shift the PDF such that the negative areas can be set to 0, while the positive area is
    #still normalized to 1
    if self.positiveShift:
        if len(where(self.pdf < 0)[0]) != 0:
            #Define a function f(delta), such that f(delta) is how far off self.pdf-delta is
            #from being normalized; hence, we want to find the zero of this function
            def normFunc(delta):
                """Calculate how far off from normal is the shifted PDF"""
                ipos = where((self.pdf-delta) >= 0.0)
                return 1 - sum((self.pdf[ipos]-delta)*prod(self.deltaX))
            #Set the initial guess for the newton-raphson search
            #a = -normFunc(0)
            a = 0.0
            #Find the zero of the above function; i.e., find delta, such that the shifted PDF is
            #normalized
            delta = newton(normFunc,a,maxiter=10000)

            #Shift the PDF
            self.pdf -= delta
            #And set the negative values to 0
            self.pdf[where(self.pdf < 0)] = 0.0

    if(self.beVerbose):
      normConst = sum(pdf*prod(self.deltaX))
      midPointAccessor = tuple([(tp-1)/2 for tp in self.numTPoints])
      print "Normalization of pdf = {}. phiSC[0] = {}".format(normConst,self.phiSC[midPointAccessor])


    #Set self.fSC for backward compatibility
    self.fSC = self.pdf

    #Take the transform of the self-consistent kernel if flagged
    if(self.doSaveTransformedKernel):
      kSC = fft.fftshift(real(fft.fftn(fft.ifftshift(self.kappaSC))))*prod(self.deltaT)*(1./(2*pi))**self.numVariables
      kSC /= prod(self.dataNorm)
      self.kSC = kSC.transpose()

  def getTransformedPDF(self):
      """Returns a copy of the PDF.  This function exists for backward compatibility"""
      return array(self.pdf)

  def getTransformedAxes(self):
      """Returns a copy of the axes.  This function exists for backward compatibility"""
      return tuple([array(xg) for xg in self.axes])

  def getTransformedCopula(self,data=None):
      """A wrapper for getCopula; this function is deprecated."""
      return self.getCopula(data)

  def estimateConditionals(self,variables,data,peakFrac = 0.0,reApplyFilter=False):
      """For a multidimensional PDF, estimates the conditional P(x_i | x_j).
      
        input:
        ------

            variables   :   A integer or tuple of array indicies indicating the variables
                            on which to condition e.g., For a 2D PDF,

                            obj.estimateConditionals(1) estimates
                            P(x_0 | x_1) from the joint PDF P(x_0,x_1) that is
                            the result of the self-consistent density estimate.

                            For a 3D PDF:

                            obj.estimateConditionals( (0,2) ) estimates 
                            P( x_0, x_2 | x_1) from P(x_0,x_1,x_2)

                            If all possible variables are listed, the copula
                            is returned instead.

                            If negative values are provided, variables are wrapped
                            (i.e., index -1 indicates the last variable)

            data        :   The data original used to create the
                            selfConsistentDensityEstimate object.  This is
                            needed to calculated the various marginals required
                            in the conditional computation.                            

            peakFrac    :   The fractional threshold below which to truncate the
                            marginal PDF (to avoid divding by small numbers);
                            this is the fraction of the height of the mode.

            reapplyFilter : Flags whether to reapply the ECF filter to the conditional

        output:
        -------


            Returns P( x_i | x_j )
      
      """

      #If the data are univariate, simply return the PDF itself
      if(self.numVariables == 1):
        return self.pdf


      #Check that we can interpret the variables tuple
      try:
          len(variables)
      except: 
          try:
              range(variables)
              variables = (variables,)
          except:
              raise ValueError,"variables appears to be neither a tuple or an integer"

      #Check that the variable indices are sane
      rightSideVariableIndices = []
      for ind in tuple(variables):
          if ind > self.numVariables-1:
              raise ValueError,"out-of-bounds positive index found in 'variables'"
          if ind < 0:
              dum = self.numVariables + ind
              if dum < 0:
                  raise ValueError,"out-of-bounds negative index found in 'variables'"
          else:
              dum = ind
          rightSideVariableIndices.append(dum)

      #Pull the unique indices and make sure they are sorted
      rightSideVariableIndices = tuple(sorted(list(set(rightSideVariableIndices))))
      if len(rightSideVariableIndices) > self.numVariables:
          raise ValueError,"More indices were provided in 'variables' than there are variables."

      #Check if all variables were provided
      if len(rightSideVariableIndices) == self.numVariables:
          return self.getCopula(data)

      #If there are no right side variables, return the PDF
      if len(rightSideVariableIndices) == 0:
          return self.pdf

      #Create the list of left-side variable indices
      leftSideVariableIndices = range(self.numVariables)
      for ind in sorted(rightSideVariableIndices)[::-1]:
          leftSideVariableIndices.pop(ind)

      #Calculate the marginal PDF
      marginalObject = selfConsistentDensityEstimate(   data[rightSideVariableIndices,:], \
                                                        axes = [self.axes[i] for i in rightSideVariableIndices], \
                                                        positiveShift = self.positiveShift, \
                                                        fracContiguousHyperVolumes = self.fracContiguousHyperVolumes, \
                                                        doSaveMarginals = False)

      #Make the shape of the new marginal object match that of the original PDF
      #(using the magic of the numpy newaxis)
      conformantSlice = list(self.numVariables*(slice(None,None,None),))
      #Insert a newaxis for each of the left-side indices
      sumAxes = []
      for ind in leftSideVariableIndices:
          #The PDF object has var0 in its rightmost axis, so transform ind
          #accordingly (it references as though var0 is the leftmost axis)
          ip = self.numVariables - ind - 1
          conformantSlice[ip] = newaxis
          #Add this index to the list of axes over which to sum for normalization
          sumAxes.append(ip)
      conformantSlice = tuple(conformantSlice)

      marginalThreshold = peakFrac*amax(marginalObject.pdf)
      #Create and mask the marginal PDF
      marginalPDF = ma.masked_less_equal(marginalObject.pdf[conformantSlice],marginalThreshold)

      #Calculate the conditional PDF
      conditionalPDF = ma.array(self.pdf)/marginalPDF

      #Refilter the conditional
      if(reApplyFilter):
          conditionalPDF = ma.masked_less_equal(self.reApplyFilter(conditionalPDF),0.0)

      #Calculate the normalization matrix
      normFactor = ma.masked_equal(sum(conditionalPDF*prod(self.deltaX[leftSideVariableIndices]),axis=tuple(sumAxes)),0.0)

      #Normalize the conditional PDF for the leftside variables
      conditionalPDF /= normFactor[conformantSlice]

      return conditionalPDF


  #*****************************************************************************
  #** selfConsistentDensityEstimate: *******************************************
  #******************* getCopula      ******************************************
  #*****************************************************************************
  #*****************************************************************************
  def getCopula(self,data=None,peakFrac = 0.0):
    """Estimates the copula of the underlying PDF"""

    #If the data are univariate, simply return the PDF itself
    if(self.numVariables == 1):
      return self.pdf

    #Check if we need to calculate the marginal distributions
    if(not self.doSaveMarginals):
      if(data is None):
        raise ValueError,"the data must be provided as argument 'data', if doSaveMarginals=False when the original PDF was calculated"
      else:
        #Estimate the marginal distributions
        marginalObjects = []
        for i in xrange(self.numVariables):
          marginalObjects.append(selfConsistentDensityEstimate(data[i,:], \
                                      axes = [self.axes[i]], \
                                      positiveShift = self.positiveShift, \
                                      fracContiguousHyperVolumes = self.fracContiguousHyperVolumes, \
                                      doSaveMarginals = False))
    else:
      #If not, just use the saved marginals
      marginalObjects = self.marginalObjects

    #Calculate the marginal distributions and mask bad (or zero) values
    marginals = []
    for obj in marginalObjects:
      #Add the marginal to the list while masking <0 values
      marginalThreshold = peakFrac*amax(obj.pdf)
      #Create and mask the marginal PDF
      marginals.append(ma.masked_less_equal(obj.pdf,marginalThreshold))

    #Calculate the PDF assuming independent marginals
    independencePDF = ma.prod(meshgrid(*tuple(marginals)),axis=0)
    #Divide off the indepdencnce PDF to calculate the copula
    #actualPDF = ma.array(self.pdf)
    #actualPDF[self.findBadDistributionInds()] = ma.masked
    actualPDF = ma.array(self.pdf)
    copulaPDF = actualPDF/independencePDF

    return copulaPDF

  def reApplyFilter(self,pdf):
      """Reapplies the filter to a PDF estimate.

      This is used, e.g., to remove high-frequency noise that results from calculting the conditionals.
      """

      #Transform the PDF to fourier space
      phiTilde_tmp = fft.fftshift(fft.ifftn(fft.ifftshift(ma.filled(pdf,0.0))))
      #Normalize the transform
      midPointAccessor = tuple([(tp-1)/2 for tp in self.numTPoints])
      phiTilde_tmp /= phiTilde_tmp[midPointAccessor]

      #Reapply the filter
      phiTilde = (0.0+0.0j)*zeros(shape(phiTilde_tmp))
      phiTilde[self.iCalcPhi] = phiTilde_tmp[self.iCalcPhi]      

      #Transform back to real space
      #Transform the PDF estimate to real space
      pdf = fft.fftshift(real(fft.fftn(fft.ifftshift(phiTilde))))*prod(self.deltaT)*(1./(2*pi))**self.numVariables
  
      #Return the transpose of the PDF
      return pdf.transpose()

  #*****************************************************************************
  #** selfConsistentDensityEstimate: ***********************************************
  #******************* Addition operator __add__ *******************************
  #*****************************************************************************
  #*****************************************************************************
  def __add__(self,rhs):
    """ Addition operator for the selfConsistentDensityEstimate object.  Adds the
        empirical characteristic functions of the two estimates, reapplies
        the BP11 filter, and transforms back to real space.  This is useful
        for parallelized calculation of densities.  Note that this only works
        if the axes are the same for both operands."""
    #Check for proper typing
    if(not isinstance(rhs,selfConsistentDensityEstimate)):
      raise TypeError, "unsupported operand type(s) for +: {} and {}".format(type(self),type(rhs))

    #Check that the axes are the same for both objects
    for sxg,rxg in zip(self.axes,rhs.axes):
        if not all(isclose(sxg,rxg)):
            raise NotImplementedError,"addition for operands with different axes is not yet implemented."

    retObj = copy.deepcopy(self)
    retObj.phiSC = (0.0+0.0j)*zeros(self.numTPoints)

    retObj.numDataPoints += rhs.numDataPoints

    #Convert the returned variance back into standard deviation
    retObj.dataStandardDeviation = sqrt(retObj.dataStandardDeviation)

    #Average the Empirical Characteristic Function of the two objects
    retObj.ECF = (self.numDataPoints*self.ECF + rhs.numDataPoints*rhs.ECF) \
                /retObj.numDataPoints

    if(retObj.doFFT):
      retObj.applyBernacchiaFilter()
      retObj.__transformphiSC__()

    #Return the new object
    return retObj

def pdf(*args,**kwargs):
    """Estimate the self-consistent kernel density estimate of the input data

        input:
        ------
            
            var1            :   An input variable.

            var2, var3...   :   Additional input varibles whose length
                                corresponds to the length of var1.  As input
                                variables are added, the dimensionality of the
                                resulting PDF increases (e.g., supplying var1
                                and var2 results in a 2D PDF).

            numPoints       :   The number of points for each axis in the PDF.
                                By default this is automatically set to an
                                optimal value for each axis.

        returns:
        --------

            pdf,axes    :       The pdf and the axes of the PDF (i.e., this is
                                analogous to hist,bins for a histogram).

                                If there are multiple input variables, the axes
                                variable is a list of the axes, with each axis
                                corresponding to an input variable.


        NOTE: The computational expense and the memory requirement of this
        method grows exponentially with the number of input variables.
    """

    #Try to get var1 from the args or kwargs
    try:
        var1 = args[0]
    except:
        try:
            var1 = kwargs['var1']
        except:
            raise ValueError,"No input data were provided."

    #Check that var1 is arraylike
    try:
        var1Shape = shape(var1)
    except BaseException as e:
        print e
        raise ValueError,"Could not get shape of var1; it does not appear to be array-like."

    #Check that var1 is a vector
    if len(var1Shape) != 1:
        raise ValueError,"var1 should be a vector.  If multiple variables are combined in a single array, please use the selfConsistentDensityEstimate class interface instead."

    #Get the length of var1
    N = var1Shape[0]

    #Check for input varibles provided as key word arguments
    varArgs = []
    varKeys = sorted([ v for v in kwargs if "var" in v ])
    for key in varKeys:
        #Ignore var1 since this was either provided as an argument 
        #or was read as a keyword argument above
        if key != "var1":
            try:
                varNum = int(key[3:])
            except BaseException as e:
                print e
                raise ValueError,"Incomprehensible variable-like keyword provided: {}".format(key)

            #Append this variable
            varArgs.append(kwargs[key])

    #Check if a mixture of keyword and arguments were provided for additional variables
    if len(varArgs) != 0 and len(args) > 1:
        raise ValueError,"additional variables were provided as a mixture of arguments and keyword arguments.  They all must be one or the other."

    #Set the additional variables to be the rest of the input arguments
    #if none were provided as key word arguments
    if len(args) > 1:
        varArgs = args[1:]

    #Start preparing the input data for
    #concatenation
    inputVariables = array(var1[newaxis,:])

    #Attempt to read additional variables
    #and concatenate them to the input variable
    for i in range(len(varArgs)):
        try:
            varn = array(varArgs[i][newaxis,:])
        except BaseException as e:
            print e
            raise ValueError,"Could not convert var{} into a numpy arrray".format(i+1)
            
        lenN = shape(varn)[1] 
        if lenN != N:
            raise ValueError,"len(var{}) is {}, but it should be the same of len(var1) = {}".format(i+1,lenN,N)

        inputVariables = concatenate((inputVariables,varn))


    #Read the optional keyword argument numPoints
    try:
        numPoints = int(kwargs['numPoints'])
    except:
        numPoints = None
    
    #Calculate the PDF
    _pdfobj = selfConsistentDensityEstimate(inputVariables, \
                                            numPoints = numPoints, \
                                            doSaveMarginals = False, \
                                            positiveShift=True, \
                                            )
                                            

    if len(_pdfobj.axes) == 1:
        return _pdfobj.pdf, _pdfobj.axes[0]
    else:
        return _pdfobj.pdf, _pdfobj.axes


#*******************************************************************************
#*******************************************************************************
#***************************** Unit testing code *******************************
#*******************************************************************************
#*******************************************************************************
# Test this implementation of the BP11 density estimate against a normal 
# distribution.  Calculate the estimate for a variety of sample sizes and show
# how the distribution error decreases as sample size increases.  As of revision
# 9 of the code, this unit testing shows that this implementation of the BP11
# estimate converges on the true normal distribution like N**-1, which agrees
# the theoretical and empirical convergence rate given in BP11.
if(__name__ == "__main__"):

  #set a seed so that results are repeatable
  random.seed(0)

  doOneDimensionalTests = True
  if(doOneDimensionalTests):
    import pylab as P
    import scipy.stats as stats

    mu = -1e3
    sig = 1e3
    #Define a gaussian function for evaluation purposes
    def mygaus(x):
      return (1./(sig*sqrt(2*pi)))*exp(-(x-mu)**2/(2.*sig**2))
    
    #Set the size of the sample to calculate
    powmax = 19
    npow = asarray(range(powmax)) + 1.0

    #Set the maximum sample size
    nmax = 2**powmax
    #Create a random normal sample of this size
    randsample = sig*random.normal(size = nmax) + mu


    #Pre-define sample size and error-squared arrays
    nsample = zeros([len(npow)])
    esq = zeros([len(npow)])
    epct = zeros([len(npow)])

    evaluateError = True
    if evaluateError:
        #Do the optimal calculation on a number of different random draws
        for i,n in zip(range(len(npow)),npow):
          #Extract a sample of length 2**n + 1 from the previously-created
          #random sample
          randgauss = randsample[:(2**n + 1)]
          #Set the sample size
          nsample[i] = len(randgauss)

          with Timer(nsample[i]):
            #Do the BP11 density estimate
            bkernel = selfConsistentDensityEstimate(randgauss,doApproximateECF=True,numPoints=513)

          #Calculate the mean squared error between the estimated density
          #And the gaussian
          #esq[i] = average(abs(mygaus(bkernel.x)-bkernel.pdf)**2 *bkernel.deltaX)
          esq[i] = average(abs(mygaus(bkernel.axes[0])-bkernel.pdf[:])**2 *bkernel.deltaX[0])
          epct[i] = 100*sum(abs(mygaus(bkernel.axes[0])-bkernel.pdf[:])*bkernel.deltaX[0])
          #Print the sample size and the error to show that the code is proceeeding
          #print "{}, {}%".format(nsample[i],epct[i])

          #Plot the optimal distribution
          P.subplot(2,2,1)#,yscale="log")
          #pdfmask = ma.masked_less(bkernel.pdf,bkernel.distributionThreshold)
          pdfmask = bkernel.pdf
          P.plot(bkernel.axes[0],pdfmask,'b-')

          #Plot the empirical characteristic function
          P.subplot(2,2,2,xscale="log",yscale="log")
          P.plot(bkernel.tgrids[0][1:],abs(bkernel.ECF[1:])**2,'b-')

        #Plot the sample gaussian
        P.subplot(2,2,1)#,yscale="log")
        P.plot(bkernel.axes[0],mygaus(bkernel.axes[0]),'r-')


        #Do a simple power law fit to the scaling
        [m,b,_,_,_] = stats.linregress(log(nsample),log(esq))
        #Print the error scaling (following BP11, this is expected to be m ~ -1)
        print "Error scales ~ N**{}".format(m)

        #Plot the error vs sample size on a log-log curve
        P.subplot(2,2,3)
        P.loglog(nsample,esq)
        P.plot(nsample,exp(b)*nsample**m,'r-')

        print ""

        bDemoSum = False
        if(not bDemoSum):
          P.show() 
        else:
          #*********************************************************************
          # Demonstrate the capability to sum selfConsistentDensityEstimate objects
          #*********************************************************************

          nsamp = 512
          nloop = nmax/nsamp


          #Pre-define sample size and error-squared arrays
          nsample2 = zeros([nloop])
          esq2 = zeros([nloop])

          for i in range(nloop):
            randgauss = randsample[i*nsamp:(i+1)*nsamp]
            if(i == 0):
              bkernel2 = selfConsistentDensityEstimate(randgauss)
              nsample2[i] = len(randgauss)
            else:
              bkernel2 += selfConsistentDensityEstimate(randgauss)
              nsample2[i] = nsample2[i-1] + len(randgauss)

            #Calculate the mean squared error between the estimated density
            #And the gaussian
            esq2[i] = average(abs(mygaus(bkernel2.axes[0])-bkernel2.pdf)**2 * bkernel2.deltaX[0])
            #Print the sample size and the error to show that the code is proceeeding
            print "{}, {}".format(nsample2[i],esq2[i])

          #Plot the distribution
          P.subplot(2,2,1)
          P.plot(bkernel2.axes[0],bkernel2.pdf,'g-')

          #Plot the ECF
          P.subplot(2,2,2,xscale="log",yscale="log")
          P.plot(bkernel2.tgrids[0][1:],abs(bkernel2.ECF[0,1:])**2,'b-')

          #Plot the error-rate change
          P.subplot(2,2,3)
          P.loglog(nsample2,esq2,'g-')

          #Plot the difference between the two distributions
          P.subplot(2,2,4)
          P.plot(bkernel2.axes[0], abs(bkernel.pdf - bkernel2.pdf)*bkernel.deltaX[0])


          #Show the plots
          P.show()
    else:
        print randsample
        #Simply do the BP11 density estimate and plot it
        bkernel = selfConsistentDensityEstimate(randsample,\
                                                doApproximateECF=True, \
                                                beVerbose = True, \
                                                numPoints = 513)
        #Plot the optimal distribution
        P.subplot(2,1,1)
        #pdfmask = ma.masked_less(bkernel.pdf,bkernel.distributionThreshold)
        pdfmask = bkernel.pdf
        P.plot(bkernel.axes[0],pdfmask,'b-')
        #Plot the sample gaussian
        P.plot(bkernel.axes[0],mygaus(bkernel.axes[0]),'r-')

        #for d in randsample:
        #    P.plot([d,d],[0,1./len(randsample)],'k-',alpha=0.5)

        #Plot the transforms
        P.subplot(2,1,2)
        P.plot(bkernel.tgrids[0],abs(bkernel.phiSC),'b-')
        ecfStandard = fft.ifft(mygaus(bkernel.axes[0]))
        ecfStandard /= ecfStandard[0]
        ecfStandard = fft.fftshift(ecfStandard)
        P.plot(bkernel.tgrids[0],abs(ecfStandard),'r-')

        mean = sum(bkernel.axes[0]*bkernel.pdf*bkernel.deltaX[0])

        P.show()

  doTwoDimensionalTests = True
  if(doTwoDimensionalTests):
    from mpl_toolkits.mplot3d import Axes3D
    import matplotlib.pyplot as plt
    import scipy.stats as stats

    nvariables = 2
    #Seed with 0 so results are reproducable
    random.seed(0)

    #Define a bivariate normal function
    def norm2d(x,y,mux=0,muy=0,sx=1,sy=1,r=0):
      coef = 1./(2*pi*sx*sy*sqrt(1.-r**2))
      expArg = -(1./(2*(1-r**2)))*( (x-mux)**2/sx**2 + (y-muy)**2/sy**2 - 2*r*(x-mux)*(y-muy)/(sx*sy))
      return coef*exp(expArg)
    
    #Set the size of the sample to calculate
    powmax = 16
    npow = asarray(range(1,powmax)) + 1.0

    #Set the maximum sample size
    nmax = 2**powmax

    def covMat(sx,sy,r):
      return [[sx**2,r*sx*sy],[r*sx*sy,sy**2]]

    gausParams = []
    gausParams.append([0.0,0.0,1.0,1.0,0.0]) #Standard, uncorrelated bivariate
    gausParams.append([2.0,0.0,1.0,1.0,0.7]) #correlation 0.7, mean x+2
    gausParams.append([0.0,2.0,1.0,0.5,0.0]) #Flat in y-direction, mean y+2
    gausParams.append([2.0,2.0,0.5,1.0,0.0]) #Flat in x-direction, mean xy+2

    #Define the corresponding standard function
    def pdfStandard(x,y):
      pdfStandard = zeros(shape(x))
      for gg in gausParams:
        pdfStandard += norm2d(x2d,y2d,*tuple(gg))*(1./ngg)

      return pdfStandard


    #Generate samples from this distribution
    randsamples = []
    ngg = len(gausParams)
    for gg in gausParams:
      mu = gg[:2]
      gCovMat = covMat(*tuple(gg[2:]))
      size = tuple([2,nmax/ngg])
      #Append a 2D gaussian to the list
      randsamples.append(random.multivariate_normal(mu,gCovMat,(nmax/ngg,)).transpose())

    #Concatenate the gaussian samples
    randsample = concatenate(tuple(randsamples),axis=1)

    #Shuffle the samples along the long axis so that we
    #can draw successively larger samples
    ishuffle = asarray(range(nmax))
    random.shuffle(ishuffle)
    randsample = randsample[:,ishuffle]

    doSaveCSV = False
    if(doSaveCSV):
        savetxt("bp11_2d_samples.csv",randsample.transpose(),delimiter=",")

    #Pre-define sample size and error-squared arrays
    nsample = zeros([len(npow)])
    esq = zeros([len(npow)])
    epct = zeros([len(npow)])

    evaluateError = True
    if(evaluateError):
      #Do the optimal calculation on a number of different random draws
      for z,n in zip(range(len(npow)),npow):
        #Extract a sample of length 2**n + 1 from the previously-created
        #random sample
        randsub = randsample[:,:(2**n)]
        #Set the sample size
        nsample[z] = shape(randsub)[1]

        with Timer(nsample[z]):
            #Do the BP11 density estimate
            bkernel = selfConsistentDensityEstimate(  randsub,  \
                                                beVerbose=False, \
                                                doSaveMarginals = False, \
                                                numPoints=129)

        x,y = tuple(bkernel.axes)
        x2d,y2d = meshgrid(x,y)

        #Calculate the mean squared error between the estimated density
        #And the gaussian
        #esq[z] = average(abs(mygaus(bkernel.x)-bkernel.pdf)**2 *bkernel.deltaX)
        #esq[z] = average(abs(pdfStandard(x2d,y2d)-bkernel.getTransformedPDF())**2 *bkernel.deltaX**2)
        absdiffsq = abs(pdfStandard(x2d,y2d)-bkernel.pdf)**2
        dx = x[1] - x[0]
        dy = y[1] - y[0]
        esq[z] = sum(dy*sum(absdiffsq*dx,axis=0))/(len(x)*len(y))
        #Print the sample size and the error to show that the code is proceeeding
        #print "{}: {}, {}".format(n,nsample[z],esq[z])

      #Do a simple power law fit to the scaling
      [m,b,_,_,_] = stats.linregress(log(nsample),log(esq))
      #Print the error scaling (following BP11, this is expected to be m ~ -1)
      print "Error scales ~ N**{}".format(m)
    else:
      with Timer(shape(randsample)[1]):
        bkernel = selfConsistentDensityEstimate(  randsample,  \
                                              beVerbose=True, \
                                              doSaveMarginals=False, \
                                              numPoints = 129)




    doPlot = True
    if(doPlot):

      x,y = tuple(bkernel.axes)
      x2d,y2d = meshgrid(x,y)

      fig = plt.figure()
      ax1 = fig.add_subplot(121)
      clevs = asarray(range(2,10))/100.
      ax1.contour(x2d,y2d,bkernel.pdf,levels = clevs)
      ax1.contour(x2d,y2d,pdfStandard(x2d,y2d),levels=clevs,colors='k')
      #ax1.plot(randsample[0,:],randsample[1,:],'k.',markersize=1)
      plt.xlim([-4,6])
      plt.ylim([-4,6])

      if(evaluateError):
        #Plot the error vs sample size on a log-log curve
        ax3 = fig.add_subplot(122,xscale="log",yscale="log")
        ax3.plot(nsample,esq)
        ax3.plot(nsample,exp(b)*nsample**m,'r-')
        #ax3 = fig.add_subplot(223)
        #ax3.plot(randsample[0,::16],randsample[1,::16],'k.',markersize=1)
        #plt.xlim([-4,6])
        #plt.ylim([-4,6])
      else:
        ax3 = fig.add_subplot(122)
        errorStandardSum= sum(abs(pdfStandard(x2d,y2d)-bkernel.pdf)**2,axis=0)
        ax3.plot(x,errorStandardSum)



      plt.show()
